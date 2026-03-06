"""
audio_listener.py -- Captura de audio del sistema y deteccion de cast/bite.

Utiliza sounddevice con WASAPI loopback para capturar el audio de salida
del sistema (NO microfono). Implementa una maquina de estados por cast que
distingue entre el pico de splash del cast (primer pico) y el pico del
bite del pez (segundo pico), usando umbrales dinamicos basados en la
baseline RMS real (~0.00002 en idle).

Modelo de reset-level:
  Despues de cada pico, el RMS debe volver a caer por debajo
  del *reset_level* (baseline_rms * reset_factor) antes de que
  se acepte el siguiente pico.  Esto evita que reverberaciones
  o picos prolongados se interpreten como dos eventos separados.

Perfil RMS observado:
  - Idle / fondo:  ~0.000017 - 0.000020
  - Cast splash:   0.001 - 0.01+  (primer pico, IGNORAR)
  - Fish bite:     0.001 - 0.01+  (segundo pico, LOOTEAR)
"""

from __future__ import annotations

import enum
import logging
import time
from typing import Any, Optional

import numpy as np
import sounddevice as sd

logger = logging.getLogger("fishing_bot.audio")


# ---------------------------------------------------------------------------
# Maquina de estados por cast
# ---------------------------------------------------------------------------


class CastState(enum.Enum):
    """Estados del ciclo de deteccion de audio por cada cast."""

    WAITING_FOR_CAST_SPLASH = "waiting_for_cast_splash"
    IGNORING_FIRST_PEAK = "ignoring_first_peak"
    WAITING_FOR_RESET = "waiting_for_reset"
    WAITING_FOR_BITE = "waiting_for_bite"
    BITE_DETECTED = "bite_detected"
    TIMED_OUT = "timed_out"


class AudioListener:
    """Captura audio del sistema y detecta el ciclo cast-splash -> bite."""

    # -----------------------------------------------------------------
    # Valores por defecto calibrados con datos reales
    # -----------------------------------------------------------------
    DEFAULT_CAST_THRESHOLD_FACTOR: float = 300.0
    DEFAULT_BITE_THRESHOLD_FACTOR: float = 500.0
    DEFAULT_MIN_ABSOLUTE_THRESHOLD: float = 0.005
    DEFAULT_IGNORE_AFTER_CAST_SECS: float = 0.5
    DEFAULT_BITE_DETECTION_TIMEOUT: float = 20.0
    DEFAULT_RESET_FACTOR: float = 2.0

    def __init__(
        self,
        device_index: Optional[int] = None,
        sample_rate: int = 44100,
        block_size: int = 1024,
        rms_threshold: float = 0.02,
        post_detection_cooldown: float = 3.0,
        cast_threshold_factor: float = DEFAULT_CAST_THRESHOLD_FACTOR,
        bite_threshold_factor: float = DEFAULT_BITE_THRESHOLD_FACTOR,
        min_absolute_threshold: float = DEFAULT_MIN_ABSOLUTE_THRESHOLD,
        ignore_after_cast_seconds: float = DEFAULT_IGNORE_AFTER_CAST_SECS,
        bite_detection_timeout_secs: float = DEFAULT_BITE_DETECTION_TIMEOUT,
        reset_factor: float = DEFAULT_RESET_FACTOR,
    ) -> None:
        """Inicializa el listener de audio con modelo de dos picos y reset-level.

        Args:
            device_index: Indice del dispositivo de audio (None = auto-seleccionar).
            sample_rate: Frecuencia de muestreo en Hz.
            block_size: Tamano de bloque para analisis de audio.
            rms_threshold: Umbral RMS legacy (mantenido por compatibilidad).
            post_detection_cooldown: Segundos de cooldown post-deteccion de bite.
            cast_threshold_factor: Multiplo de baseline para umbral de cast.
            bite_threshold_factor: Multiplo de baseline para umbral de bite.
            min_absolute_threshold: Umbral minimo absoluto (floor).
            ignore_after_cast_seconds: Ventana de silencio tras el pico de cast.
            bite_detection_timeout_secs: Timeout global para detectar el bite.
            reset_factor: Multiplo de baseline para el nivel de reset.
        """
        self.device_index: Optional[int] = device_index
        self.sample_rate: int = sample_rate
        self.block_size: int = block_size
        self.rms_threshold: float = rms_threshold
        self.post_detection_cooldown: float = post_detection_cooldown

        # Parametros del modelo de dos picos
        self.cast_threshold_factor: float = cast_threshold_factor
        self.bite_threshold_factor: float = bite_threshold_factor
        self.min_absolute_threshold: float = min_absolute_threshold
        self.ignore_after_cast_seconds: float = ignore_after_cast_seconds
        self.bite_detection_timeout_secs: float = bite_detection_timeout_secs
        self.reset_factor: float = reset_factor

        # Estado interno del stream
        self._stream: Optional[sd.InputStream] = None
        self._baseline_rms: float = 0.0
        self._reconnect_attempts: int = 0
        self._max_reconnects: int = 3
        self._running: bool = False
        self._rms_history: list[float] = []

        # Estado de la maquina de estados por cast
        self._cast_state: CastState = CastState.TIMED_OUT
        self._cast_splash_time: float = 0.0
        self._bite_detected: bool = False
        self._last_rms: float = 0.0

        # Legacy: mantener para compatibilidad con wait_for_splash()
        self._splash_detected: bool = False
        self._last_detection_time: float = 0.0

    # -----------------------------------------------------------------
    # Propiedades de umbrales dinamicos
    # -----------------------------------------------------------------

    @property
    def cast_threshold(self) -> float:
        """Umbral dinamico para detectar el pico de cast splash."""
        return max(
            self.min_absolute_threshold,
            self._baseline_rms * self.cast_threshold_factor,
        )

    @property
    def bite_threshold(self) -> float:
        """Umbral dinamico para detectar el pico de fish bite."""
        return max(
            self.min_absolute_threshold,
            self._baseline_rms * self.bite_threshold_factor,
        )

    @property
    def reset_level(self) -> float:
        """Nivel de reset: RMS debe caer por debajo de este valor
        despues de un pico antes de aceptar el siguiente.

        Returns:
            max(baseline_rms * reset_factor, baseline_rms * 2)
        """
        return max(
            self._baseline_rms * self.reset_factor,
            self._baseline_rms * 2.0,
        )

    # -----------------------------------------------------------------
    # Dispositivos
    # -----------------------------------------------------------------

    @staticmethod
    def list_loopback_devices() -> list[dict[str, Any]]:
        """Lista todos los dispositivos de entrada disponibles."""
        devices: list[dict[str, Any]] = []
        all_devices = sd.query_devices()

        if not isinstance(all_devices, list):
            all_devices = [all_devices]

        for i, dev in enumerate(all_devices):
            max_input = dev.get("max_input_channels", 0)
            if max_input and max_input > 0:
                devices.append({
                    "index": i,
                    "name": dev["name"],
                    "channels": max_input,
                    "samplerate": dev["default_samplerate"],
                })

        return devices

    @staticmethod
    def find_loopback_device() -> Optional[int]:
        """Busca automaticamente un dispositivo de loopback WASAPI / Stereo Mix."""
        devices = AudioListener.list_loopback_devices()
        priority_keywords = [
            "steam streaming speakers",
            "wasapi loopback",
            "loopback",
            "stereo mix",
            "mezcla estereo",
            "wave out",
            "what u hear",
            "what you hear",
        ]

        for keyword in priority_keywords:
            for dev in devices:
                if keyword in dev["name"].lower():
                    logger.info(
                        "Dispositivo loopback encontrado: [%d] %s",
                        dev["index"],
                        dev["name"],
                    )
                    return dev["index"]

        return None

    def log_device_info(self) -> None:
        """Registra en log el nombre real del dispositivo de audio configurado."""
        if self.device_index is not None:
            try:
                dev_info = sd.query_devices(self.device_index)
                logger.info(
                    "Dispositivo de audio confirmado: [%d] %s "
                    "(inputs=%d, rate=%.0f Hz)",
                    self.device_index,
                    dev_info["name"],
                    dev_info["max_input_channels"],
                    dev_info["default_samplerate"],
                )
            except Exception as exc:
                logger.warning(
                    "No se pudo consultar dispositivo %d: %s",
                    self.device_index,
                    exc,
                )

    # -----------------------------------------------------------------
    # Callback de audio (alimenta RMS + maquina de estados)
    # -----------------------------------------------------------------

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: Any,
        status: sd.CallbackFlags,
    ) -> None:
        """Callback del stream de audio -- calcula RMS y actualiza baseline.

        La logica de la maquina de estados se ejecuta en el hilo principal
        (wait_for_cast_and_bite) para evitar problemas de concurrencia.
        """
        if status:
            logger.warning("Estado del stream de audio: %s", status)

        if indata is None or len(indata) == 0:
            return

        # Calcular RMS del bloque actual
        rms: float = float(np.sqrt(np.mean(indata.astype(np.float64) ** 2)))
        self._last_rms = rms

        # Mantener historial para baseline adaptativo (ultimos 100 bloques)
        self._rms_history.append(rms)
        if len(self._rms_history) > 100:
            self._rms_history.pop(0)

        # Actualizar baseline solo con bloques que NO sean spikes
        # (usa los 50 valores mas bajos del historial para no contaminar)
        if len(self._rms_history) >= 10:
            sorted_recent = sorted(self._rms_history)
            low_half = sorted_recent[: len(sorted_recent) // 2]
            self._baseline_rms = float(np.mean(low_half))

    # -----------------------------------------------------------------
    # Stream management
    # -----------------------------------------------------------------

    def start_stream(self) -> None:
        """Inicia el stream de captura de audio."""
        if self._stream is not None:
            self.stop_stream()

        try:
            self._stream = sd.InputStream(
                device=self.device_index,
                channels=1,
                samplerate=self.sample_rate,
                blocksize=self.block_size,
                dtype="float32",
                callback=self._audio_callback,
            )
            self._stream.start()
            self._running = True
            self._reconnect_attempts = 0
            self._rms_history.clear()
            self._baseline_rms = 0.0
            self.log_device_info()
            logger.info(
                "Stream de audio iniciado (dispositivo=%s, rate=%d, block=%d)",
                self.device_index,
                self.sample_rate,
                self.block_size,
            )
        except Exception as exc:
            logger.error("Error al iniciar stream de audio: %s", exc)
            self._try_reconnect()

    def stop_stream(self) -> None:
        """Detiene y cierra el stream de audio de forma segura."""
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as exc:
                logger.warning("Error al cerrar stream: %s", exc)
            finally:
                self._stream = None
        logger.info("Stream de audio detenido.")

    def _try_reconnect(self) -> None:
        """Intenta reconectar el stream hasta un maximo de intentos."""
        self._reconnect_attempts += 1
        if self._reconnect_attempts > self._max_reconnects:
            msg = (
                f"No se pudo reconectar al stream de audio tras "
                f"{self._max_reconnects} intentos."
            )
            logger.critical(msg)
            raise RuntimeError(msg)

        logger.warning(
            "Reintentando conexion de audio (%d/%d)...",
            self._reconnect_attempts,
            self._max_reconnects,
        )
        time.sleep(1.0)
        self.start_stream()

    def _ensure_stream_alive(self) -> bool:
        """Verifica que el stream siga activo e intenta reconectar si no."""
        if self._stream is not None and self._stream.active:
            return True
        logger.warning("Stream de audio perdido.")
        try:
            self._try_reconnect()
            return True
        except RuntimeError:
            return False

    # -----------------------------------------------------------------
    # Metodo principal: deteccion de dos picos (cast -> bite)
    # con reset-level estricto
    # -----------------------------------------------------------------

    def wait_for_cast_and_bite(
        self,
        bite_timeout: Optional[float] = None,
        cast_splash_timeout: float = 5.0,
    ) -> bool:
        """Espera el ciclo completo: cast splash -> ignore -> reset -> bite.

        Maquina de estados:
          1. WAITING_FOR_CAST_SPLASH -- espera primer spike > cast_threshold
          2. IGNORING_FIRST_PEAK -- ventana de silencio (ignore_after_cast_seconds)
          3. WAITING_FOR_RESET -- espera que RMS caiga por debajo de reset_level
          4. WAITING_FOR_BITE -- espera segundo spike > bite_threshold
          5. BITE_DETECTED o TIMED_OUT

        Args:
            bite_timeout: Timeout global para el bite tras el cast.
                          None = usa self.bite_detection_timeout_secs.
            cast_splash_timeout: Max. segundos para detectar el primer pico
                                 de cast splash (default 5s).

        Returns:
            True si se detecto el bite (segundo pico), False si timeout.
        """
        if bite_timeout is None:
            bite_timeout = self.bite_detection_timeout_secs

        ct = self.cast_threshold
        bt = self.bite_threshold
        rl = self.reset_level

        logger.info(
            "Deteccion cast->bite | baseline=%.8f | cast_thr=%.6f | "
            "bite_thr=%.6f | reset_lvl=%.8f | ignore=%.2fs | timeout=%.1fs",
            self._baseline_rms,
            ct,
            bt,
            rl,
            self.ignore_after_cast_seconds,
            bite_timeout,
        )

        # -- FASE 1: Esperar cast splash -----------------------------------
        self._cast_state = CastState.WAITING_FOR_CAST_SPLASH
        phase1_start = time.time()

        while time.time() - phase1_start < cast_splash_timeout:
            if not self._ensure_stream_alive():
                return False

            rms = self._last_rms

            if rms > ct:
                self._cast_splash_time = time.time()
                self._cast_state = CastState.IGNORING_FIRST_PEAK
                logger.info(
                    "CAST SPLASH detectado | rms=%.8f > cast_thr=%.6f | "
                    "baseline=%.8f | t=%.3fs",
                    rms,
                    ct,
                    self._baseline_rms,
                    self._cast_splash_time - phase1_start,
                )
                break

            time.sleep(0.005)
        else:
            # No se detecto cast splash -- transicionar directo a esperar bite
            logger.info(
                "No se detecto cast splash en %.1fs -- esperando bite directamente.",
                cast_splash_timeout,
            )
            self._cast_state = CastState.WAITING_FOR_BITE
            self._cast_splash_time = time.time()

        # -- FASE 2: Ventana de silencio (ignorar reverb del cast) ----------
        if self._cast_state == CastState.IGNORING_FIRST_PEAK:
            ignore_end = self._cast_splash_time + self.ignore_after_cast_seconds
            logger.debug(
                "Ignorando picos por %.2fs (reverb del cast)...",
                self.ignore_after_cast_seconds,
            )
            while time.time() < ignore_end:
                time.sleep(0.005)
            self._cast_state = CastState.WAITING_FOR_RESET
            logger.debug("Ventana de silencio terminada -- esperando reset level.")

        # -- FASE 2.5: Esperar que RMS caiga por debajo de reset_level ------
        if self._cast_state == CastState.WAITING_FOR_RESET:
            reset_start = time.time()
            # Dar un maximo de bite_timeout para que caiga; si no, timeout.
            while time.time() - reset_start < bite_timeout:
                if not self._ensure_stream_alive():
                    return False

                rms = self._last_rms
                if rms <= rl:
                    self._cast_state = CastState.WAITING_FOR_BITE
                    logger.info(
                        "RMS volvio a reset level | rms=%.8f <= reset_lvl=%.8f | "
                        "t_reset=%.3fs",
                        rms,
                        rl,
                        time.time() - reset_start,
                    )
                    break

                time.sleep(0.005)
            else:
                self._cast_state = CastState.TIMED_OUT
                logger.info(
                    "Timeout esperando reset level (%.1fs) | "
                    "last_rms=%.8f | reset_lvl=%.8f",
                    bite_timeout,
                    self._last_rms,
                    rl,
                )
                return False

        # -- FASE 3: Esperar bite (segundo pico) ----------------------------
        bite_start = time.time()

        while time.time() - bite_start < bite_timeout:
            if not self._ensure_stream_alive():
                return False

            rms = self._last_rms
            if rms > bt:
                self._cast_state = CastState.BITE_DETECTED
                self._bite_detected = True
                elapsed_total = time.time() - phase1_start
                logger.info(
                    "BITE detectado! | rms=%.8f > bite_thr=%.6f | "
                    "baseline=%.8f | t_total=%.2fs",
                    rms,
                    bt,
                    self._baseline_rms,
                    elapsed_total,
                )
                return True

            time.sleep(0.005)

        # Timeout
        self._cast_state = CastState.TIMED_OUT
        logger.info(
            "Timeout de bite alcanzado (%.1fs) | baseline=%.8f | bite_thr=%.6f",
            bite_timeout,
            self._baseline_rms,
            bt,
        )
        return False

    # -----------------------------------------------------------------
    # Legacy: wait_for_splash (compatibilidad con codigo existente)
    # -----------------------------------------------------------------

    def wait_for_splash(self, timeout: float = 30.0) -> bool:
        """Espera hasta que se detecte un splash o se agote el timeout (LEGACY).

        Delega internamente al modelo de dos picos.
        """
        return self.wait_for_cast_and_bite(bite_timeout=timeout)

    # -----------------------------------------------------------------
    # Calibracion y limpieza periodica
    # -----------------------------------------------------------------

    def calibrate_baseline(self, duration: float = 2.0) -> float:
        """Calibra la linea base de ruido ambiental.

        Debe ejecutarse mientras WoW esta idle para obtener una
        baseline estable (~0.000017 - 0.000020).

        Args:
            duration: Duracion en segundos para la calibracion.

        Returns:
            El valor RMS promedio de la linea base calculada.
        """
        logger.info("Calibrando linea base de audio (%.1fs)...", duration)
        self._rms_history.clear()
        self._baseline_rms = 0.0
        time.sleep(duration)

        if self._rms_history:
            self._baseline_rms = float(np.mean(self._rms_history))
            ct = self.cast_threshold
            bt = self.bite_threshold
            rl = self.reset_level
            logger.info(
                "Linea base calibrada: baseline_rms=%.8f | "
                "cast_threshold=%.6f | bite_threshold=%.6f | reset_level=%.8f",
                self._baseline_rms,
                ct,
                bt,
                rl,
            )
        else:
            logger.warning("No se recibieron datos de audio durante calibracion.")

        return self._baseline_rms

    def reset_cleanup(self, calibration_duration: float = 2.0) -> float:
        """Limpieza periodica: borra historial RMS y recalibra baseline.

        Llamar cada N iteraciones para evitar drift en la baseline.

        Args:
            calibration_duration: Segundos de silencio para recalibrar.

        Returns:
            Nueva baseline RMS.
        """
        logger.info("Ejecutando limpieza periodica (reset_cleanup)...")
        self._rms_history.clear()
        self._baseline_rms = 0.0
        self._cast_state = CastState.TIMED_OUT
        self._bite_detected = False
        self._last_rms = 0.0
        return self.calibrate_baseline(duration=calibration_duration)

    # -----------------------------------------------------------------
    # Propiedades
    # -----------------------------------------------------------------

    @property
    def baseline_rms(self) -> float:
        """Valor actual de la linea base RMS."""
        return self._baseline_rms

    @property
    def last_rms(self) -> float:
        """Ultimo valor RMS leido del stream."""
        return self._last_rms

    @property
    def current_state(self) -> CastState:
        """Estado actual de la maquina de estados."""
        return self._cast_state

    @property
    def is_running(self) -> bool:
        """Indica si el stream de audio esta activo."""
        return self._running and self._stream is not None and self._stream.active
