"""
audio_listener.py — Captura de audio del sistema y detección de splash.

Utiliza sounddevice con WASAPI loopback para capturar el audio de salida
del sistema (NO micrófono). Analiza bloques de audio mediante RMS con numpy
para detectar el pico transitorio del splash del bobber de pesca.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import numpy as np
import sounddevice as sd

logger = logging.getLogger("fishing_bot.audio")


class AudioListener:
    """Captura audio del sistema y detecta el sonido de splash del bobber."""

    def __init__(
        self,
        device_index: Optional[int] = None,
        sample_rate: int = 44100,
        block_size: int = 1024,
        rms_threshold: float = 0.02,
        post_detection_cooldown: float = 3.0,
    ) -> None:
        """Inicializa el listener de audio.

        Args:
            device_index: Índice del dispositivo de audio (None = auto-seleccionar).
            sample_rate: Frecuencia de muestreo en Hz.
            block_size: Tamaño de bloque para análisis de audio.
            rms_threshold: Umbral RMS para detección de splash.
            post_detection_cooldown: Segundos de cooldown post-detección.
        """
        self.device_index: Optional[int] = device_index
        self.sample_rate: int = sample_rate
        self.block_size: int = block_size
        self.rms_threshold: float = rms_threshold
        self.post_detection_cooldown: float = post_detection_cooldown

        self._stream: Optional[sd.InputStream] = None
        self._baseline_rms: float = 0.0
        self._baseline_samples: int = 0
        self._splash_detected: bool = False
        self._last_detection_time: float = 0.0
        self._reconnect_attempts: int = 0
        self._max_reconnects: int = 3
        self._running: bool = False
        self._rms_history: list[float] = []

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
                    logger.info("Dispositivo loopback encontrado: [%d] %s", dev["index"], dev["name"])
                    return dev["index"]

        return None

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: Any,
        status: sd.CallbackFlags,
    ) -> None:
        """Callback del stream de audio — analiza cada bloque.

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

        # Mantener historial para baseline adaptativo
        self._rms_history.append(rms)
        if len(self._rms_history) > 100:
            self._rms_history.pop(0)

        # Actualizar baseline (promedio móvil)
        if len(self._rms_history) >= 5:
            self._baseline_rms = float(np.mean(self._rms_history[-50:]))

        # Verificar cooldown post-detección
        now = time.time()
        if now - self._last_detection_time < self.post_detection_cooldown:
            return

        # Detectar spike por encima del umbral
        threshold = max(self.rms_threshold, self._baseline_rms * 3.0)
        if rms > threshold and rms > self._baseline_rms + self.rms_threshold:
            self._splash_detected = True
            self._last_detection_time = now
            logger.debug(
                "¡Splash detectado! RMS=%.6f, baseline=%.6f, threshold=%.6f",
                rms,
                self._baseline_rms,
                threshold,
            )

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

    def wait_for_splash(self, timeout: float = 30.0) -> bool:
        """Espera hasta que se detecte un splash o se agote el timeout.

        Args:
            timeout: Tiempo máximo de espera en segundos.

        Returns:
            True si se detectó splash, False si expiró el timeout.
        """
        self._splash_detected = False
        start = time.time()

        logger.debug("Escuchando splash (timeout=%.1fs)...", timeout)

        while time.time() - start < timeout:
            if self._splash_detected:
                elapsed = time.time() - start
                logger.info("¡Splash detectado en %.2f segundos!", elapsed)
                return True

            # Verificar que el stream sigue activo
            if self._stream is None or not self._stream.active:
                logger.warning("Stream de audio perdido durante escucha.")
                try:
                    self._try_reconnect()
                except RuntimeError:
                    return False

            time.sleep(0.01)  # Polling rápido sin saturar CPU

        logger.info("Timeout de detección alcanzado (%.1fs).", timeout)
        return False

    def calibrate_baseline(self, duration: float = 2.0) -> float:
        """Calibra la línea base de ruido ambiental.

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
            logger.info("Línea base calibrada: RMS=%.6f", self._baseline_rms)
        else:
            logger.warning("No se recibieron datos de audio durante calibración.")

        return self._baseline_rms

    @property
    def is_running(self) -> bool:
        """Indica si el stream de audio está activo.

        Returns:
            True si el stream está corriendo.
        """
        return self._running and self._stream is not None and self._stream.active
