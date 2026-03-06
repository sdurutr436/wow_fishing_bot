"""
audio_listener.py — Captura de audio del sistema y detección de cast/bite.

Utiliza sounddevice con WASAPI loopback para capturar el audio de salida
del sistema (NO micrófono). Implementa una máquina de estados por cast que
distingue entre el pico de splash del cast (primer pico) y el pico del
bite del pez (segundo pico), usando umbrales dinámicos basados en la
baseline RMS real (~0.00002 en idle).

Perfil RMS observado:
  - Idle / fondo:  ~0.000017 – 0.000020
  - Cast splash:   0.001 – 0.01+  (primer pico, IGNORAR)
  - Fish bite:     0.001 – 0.01+  (segundo pico, LOOTEAR)
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
# Máquina de estados por cast
# ---------------------------------------------------------------------------


class CastState(enum.Enum):
    """Estados del ciclo de detección de audio por cada cast."""

    WAITING_FOR_CAST_SPLASH = "waiting_for_cast_splash"
    IGNORING_FIRST_PEAK = "ignoring_first_peak"
    WAITING_FOR_BITE = "waiting_for_bite"
    BITE_DETECTED = "bite_detected"
    TIMED_OUT = "timed_out"


class AudioListener:
    """Captura audio del sistema y detecta el ciclo cast-splash → bite."""

    # -----------------------------------------------------------------
    # Valores por defecto calibrados con datos reales
    # -----------------------------------------------------------------
    DEFAULT_CAST_THRESHOLD_FACTOR: float = 300.0
    DEFAULT_BITE_THRESHOLD_FACTOR: float = 500.0
    DEFAULT_MIN_ABSOLUTE_THRESHOLD: float = 0.005
    DEFAULT_IGNORE_AFTER_CAST_SECS: float = 0.5
    DEFAULT_BITE_DETECTION_TIMEOUT: float = 20.0

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
    ) -> None:
        """Inicializa el listener de audio con modelo de dos picos.

        Args:
            device_index: Índice del dispositivo de audio (None = auto-seleccionar).
            sample_rate: Frecuencia de muestreo en Hz.
            block_size: Tamaño de bloque para análisis de audio.
            rms_threshold: Umbral RMS legacy (mantenido por compatibilidad).
            post_detection_cooldown: Segundos de cooldown post-detección de bite.
            cast_threshold_factor: Múltiplo de baseline para umbral de cast.
            bite_threshold_factor: Múltiplo de baseline para umbral de bite.
            min_absolute_threshold: Umbral mínimo absoluto (floor).
            ignore_after_cast_seconds: Ventana de silencio tras el pico de cast.
            bite_detection_timeout_secs: Timeout global para detectar el bite.
        """
        self.device_index: Optional[int] = device_index
        self.sample_rate: int = sample_rate
        self.block_size: int = block_size
        self.rms_threshold: float = rms_threshold
        self.post_detection_cooldown: float = post_detection_cooldown

        # Parámetros del modelo de dos picos
        self.cast_threshold_factor: float = cast_threshold_factor
        self.bite_threshold_factor: float = bite_threshold_factor
        self.min_absolute_threshold: float = min_absolute_threshold
        self.ignore_after_cast_seconds: float = ignore_after_cast_seconds
        self.bite_detection_timeout_secs: float = bite_detection_timeout_secs

        # Estado interno del stream
        self._stream: Optional[sd.InputStream] = None
        self._baseline_rms: float = 0.0
        self._reconnect_attempts: int = 0
        self._max_reconnects: int = 3
        self._running: bool = False
        self._rms_history: list[float] = []

        # Estado de la máquina de estados por cast
        self._cast_state: CastState = CastState.TIMED_OUT
        self._cast_splash_time: float = 0.0
        self._bite_detected: bool = False
        self._last_rms: float = 0.0

        # Legacy: mantener para compatibilidad con wait_for_splash()
        self._splash_detected: bool = False
        self._last_detection_time: float = 0.0

    # -----------------------------------------------------------------
    # Propiedades de umbrales dinámicos
    # -----------------------------------------------------------------

    @property
    def cast_threshold(self) -> float:
        """Umbral dinámico para detectar el pico de cast splash.

        Returns:
            max(min_absolute_threshold, baseline_rms * cast_threshold_factor)
        """
        return max(
            self.min_absolute_threshold,
            self._baseline_rms * self.cast_threshold_factor,
        )

    @property
    def bite_threshold(self) -> float:
        """Umbral dinámico para detectar el pico de fish bite.

        Returns:
            max(min_absolute_threshold, baseline_rms * bite_threshold_factor)
        """
        return max(
            self.min_absolute_threshold,
            self._baseline_rms * self.bite_threshold_factor,
        )

    # -----------------------------------------------------------------
    # Dispositivos
    # -----------------------------------------------------------------

    @staticmethod
    def list_loopback_devices() -> list[dict[str, Any]]:
        """Lista todos los dispositivos de entrada disponibles que pueden usarse para loopback.

        Returns:
            Lista de diccionarios con info del dispositivo (index, name, channels, samplerate).
        """
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
        """Busca automáticamente un dispositivo de loopback WASAPI / Stereo Mix.

        Returns:
            Índice del dispositivo encontrado, o None si no se encuentra.
        """
        devices = AudioListener.list_loopback_devices()
        priority_keywords = [
            "steam streaming speakers",
            "wasapi loopback",
            "loopback",
            "stereo mix",
            "mezcla estéreo",
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
    # Callback de audio (alimenta RMS + máquina de estados)
    # -----------------------------------------------------------------

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: Any,
        status: sd.CallbackFlags,
    ) -> None:
        """Callback del stream de audio — calcula RMS y alimenta la máquina de estados.

        El callback SOLO actualiza la baseline y el último RMS.
        La lógica de la máquina de estados se ejecuta en el hilo principal
        (wait_for_cast_and_bite) para evitar problemas de concurrencia.

        Args:
            indata: Datos de audio del bloque actual.
            frames: Número de frames en el bloque.
            time_info: Información de tiempo del stream.
            status: Flags de estado del callback.
        """
        if status:
            logger.warning("Estado del stream de audio: %s", status)

        if indata is None or len(indata) == 0:
            return

        # Calcular RMS del bloque actual
        rms: float = float(np.sqrt(np.mean(indata.astype(np.float64) ** 2)))
        self._last_rms = rms

        # Mantener historial para baseline adaptativo (últimos 100 bloques)
        self._rms_history.append(rms)
        if len(self._rms_history) > 100:
            self._rms_history.pop(0)

        # Actualizar baseline sólo con bloques que NO sean spikes
        # (usa los 50 valores más bajos del historial para no contaminar)
        if len(self._rms_history) >= 10:
            sorted_recent = sorted(self._rms_history)
            # Tomar el percentil bajo (primera mitad) para baseline estable
            low_half = sorted_recent[: len(sorted_recent) // 2]
            self._baseline_rms = float(np.mean(low_half))

    # -----------------------------------------------------------------
    # Stream management
    # -----------------------------------------------------------------

    def start_stream(self) -> None:
        """Inicia el stream de captura de audio.

        Raises:
            RuntimeError: Si no se puede iniciar el stream tras múltiples intentos.
        """
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
        """Intenta reconectar el stream hasta un máximo de intentos.

        Raises:
            RuntimeError: Si se agotan los intentos de reconexión.
        """
        self._reconnect_attempts += 1
        if self._reconnect_attempts > self._max_reconnects:
            msg = (
                f"No se pudo reconectar al stream de audio tras "
                f"{self._max_reconnects} intentos."
            )
            logger.critical(msg)
            raise RuntimeError(msg)

        logger.warning(
            "Reintentando conexión de audio (%d/%d)...",
            self._reconnect_attempts,
            self._max_reconnects,
        )
        time.sleep(1.0)
        self.start_stream()

    def _ensure_stream_alive(self) -> bool:
        """Verifica que el stream siga activo e intenta reconectar si no.

        Returns:
            True si el stream está activo, False si la reconexión falló.
        """
        if self._stream is not None and self._stream.active:
            return True
        logger.warning("Stream de audio perdido.")
        try:
            self._try_reconnect()
            return True
        except RuntimeError:
            return False

    # -----------------------------------------------------------------
    # Método principal: detección de dos picos (cast → bite)
    # -----------------------------------------------------------------

    def wait_for_cast_and_bite(
        self,
        bite_timeout: Optional[float] = None,
        cast_splash_timeout: float = 5.0,
    ) -> bool:
        """Espera el ciclo completo: pico de cast splash → ignore → pico de bite.

        Implementa la máquina de estados:
          1. WAITING_FOR_CAST_SPLASH — espera el primer spike > cast_threshold
          2. IGNORING_FIRST_PEAK — ventana de silencio (ignore_after_cast_seconds)
          3. WAITING_FOR_BITE — espera el segundo spike > bite_threshold
          4. BITE_DETECTED o TIMED_OUT

        Args:
            bite_timeout: Timeout global para el bite tras el cast.
                          None = usa self.bite_detection_timeout_secs.
            cast_splash_timeout: Máx. segundos para detectar el primer pico
                                 de cast splash (default 5s).

        Returns:
            True si se detectó el bite (segundo pico), False si timeout.
        """
        if bite_timeout is None:
            bite_timeout = self.bite_detection_timeout_secs

        ct = self.cast_threshold
        bt = self.bite_threshold

        logger.debug(
            "Iniciando detección cast→bite | baseline=%.8f | "
            "cast_thr=%.6f | bite_thr=%.6f | ignore=%.2fs | timeout=%.1fs",
            self._baseline_rms,
            ct,
            bt,
            self.ignore_after_cast_seconds,
            bite_timeout,
        )

        # ── FASE 1: Esperar cast splash ──────────────────────────────
        self._cast_state = CastState.WAITING_FOR_CAST_SPLASH
        phase1_start = time.time()

        while time.time() - phase1_start < cast_splash_timeout:
            if not self._ensure_stream_alive():
                return False

            rms = self._last_rms
            if rms > ct:
                self._cast_splash_time = time.time()
                self._cast_state = CastState.IGNORING_FIRST_PEAK
                logger.debug(
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
            # No se detectó ni siquiera el cast splash — puede ser normal
            # si el volumen del juego es muy bajo. Transicionar directamente
            # a esperar bite sin exigir cast splash previo.
            logger.debug(
                "No se detectó cast splash en %.1fs — esperando bite directamente.",
                cast_splash_timeout,
            )
            self._cast_state = CastState.WAITING_FOR_BITE
            self._cast_splash_time = time.time()

        # ── FASE 2: Ventana de silencio (ignorar reverb del cast) ────
        if self._cast_state == CastState.IGNORING_FIRST_PEAK:
            ignore_end = self._cast_splash_time + self.ignore_after_cast_seconds
            logger.debug(
                "Ignorando picos por %.2fs (reverb del cast)...",
                self.ignore_after_cast_seconds,
            )
            while time.time() < ignore_end:
                time.sleep(0.005)
            self._cast_state = CastState.WAITING_FOR_BITE
            logger.debug("Ventana de silencio terminada — escuchando bite.")

        # ── FASE 3: Esperar bite (segundo pico) ─────────────────────
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
                    "¡BITE detectado! | rms=%.8f > bite_thr=%.6f | "
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
    # Legacy: wait_for_splash (compatibilidad con código existente)
    # -----------------------------------------------------------------

    def wait_for_splash(self, timeout: float = 30.0) -> bool:
        """Espera hasta que se detecte un splash o se agote el timeout (LEGACY).

        Este método ahora delega internamente al modelo de dos picos:
        ejecuta wait_for_cast_and_bite() con el timeout indicado.

        Args:
            timeout: Tiempo máximo de espera en segundos.

        Returns:
            True si se detectó el bite (segundo pico), False si timeout.
        """
        return self.wait_for_cast_and_bite(bite_timeout=timeout)

    # -----------------------------------------------------------------
    # Calibración
    # -----------------------------------------------------------------

    def calibrate_baseline(self, duration: float = 2.0) -> float:
        """Calibra la línea base de ruido ambiental.

        Debe ejecutarse mientras WoW está idle o muy silencioso para
        obtener una baseline estable (~0.000017 – 0.000020).

        Args:
            duration: Duración en segundos para la calibración.

        Returns:
            El valor RMS promedio de la línea base calculada.
        """
        logger.info("Calibrando línea base de audio (%.1fs)...", duration)
        self._rms_history.clear()
        self._baseline_rms = 0.0
        time.sleep(duration)

        if self._rms_history:
            self._baseline_rms = float(np.mean(self._rms_history))
            ct = self.cast_threshold
            bt = self.bite_threshold
            logger.info(
                "Línea base calibrada: baseline_rms=%.8f | "
                "cast_threshold=%.6f | bite_threshold=%.6f",
                self._baseline_rms,
                ct,
                bt,
            )
        else:
            logger.warning("No se recibieron datos de audio durante calibración.")

        return self._baseline_rms

    # -----------------------------------------------------------------
    # Propiedades
    # -----------------------------------------------------------------

    @property
    def baseline_rms(self) -> float:
        """Valor actual de la línea base RMS.

        Returns:
            RMS baseline actual.
        """
        return self._baseline_rms

    @property
    def last_rms(self) -> float:
        """Último valor RMS leído del stream.

        Returns:
            Último RMS.
        """
        return self._last_rms

    @property
    def current_state(self) -> CastState:
        """Estado actual de la máquina de estados.

        Returns:
            CastState actual.
        """
        return self._cast_state

    @property
    def is_running(self) -> bool:
        """Indica si el stream de audio está activo.

        Returns:
            True si el stream está corriendo.
        """
        return self._running and self._stream is not None and self._stream.active
