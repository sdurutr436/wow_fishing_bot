"""
input_handler.py — Simulacion de input para el bot de pesca.

Envia pulsaciones de tecla al juego **unicamente** con pydirectinput.press().
NO se usa SendInput, SendMessage, PostMessage ni ninguna otra API de bajo nivel.
Cada pulsacion se registra con timestamp completo a nivel INFO.
"""

from __future__ import annotations

import logging
import random
import time

logger = logging.getLogger("fishing_bot.input")


class InputHandler:
    """Gestiona el envio de teclas al juego usando solo pydirectinput."""

    def __init__(
        self,
        keybind: str = ".",
        min_human_delay: float = 0.05,
        max_human_delay: float = 0.30,
    ) -> None:
        """Inicializa el handler de input.

        Args:
            keybind: Tecla a enviar (por defecto punto).
            min_human_delay: Delay minimo humanizado en segundos.
            max_human_delay: Delay maximo humanizado en segundos.
        """
        self.keybind: str = keybind
        self.min_human_delay: float = min_human_delay
        self.max_human_delay: float = max_human_delay

        import pydirectinput

        pydirectinput.PAUSE = 0.0  # Desactivar pausa interna
        self._pydirectinput = pydirectinput
        logger.info(
            "InputHandler inicializado | keybind='%s' | delay=%.2f-%.2fs | metodo=pydirectinput.press()",
            self.keybind,
            self.min_human_delay,
            self.max_human_delay,
        )

    # ------------------------------------------------------------------
    # Metodos internos
    # ------------------------------------------------------------------

    def _human_delay(self) -> float:
        """Aplica un delay aleatorio humanizado antes de la pulsacion.

        Returns:
            El delay aplicado en segundos.
        """
        delay = random.uniform(self.min_human_delay, self.max_human_delay)
        logger.debug("Delay humanizado: %.3fs", delay)
        time.sleep(delay)
        return delay

    def _press_key(self, action_label: str) -> bool:
        """Envia la tecla usando pydirectinput.press() y registra el evento.

        Args:
            action_label: Etiqueta descriptiva (e.g. 'CAST' o 'LOOT').

        Returns:
            True si se envio correctamente, False en caso de error.
        """
        try:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            self._pydirectinput.press(self.keybind)
            logger.info(
                "%s key pressed ('%s') at %s",
                action_label,
                self.keybind,
                timestamp,
            )
            return True
        except Exception as exc:
            logger.error(
                "Error enviando tecla '%s' [%s]: %s",
                self.keybind,
                action_label,
                exc,
            )
            return False

    # ------------------------------------------------------------------
    # API publica
    # ------------------------------------------------------------------

    def send_key(self, _hwnd: object = None) -> bool:
        """Envia la tecla configurada con delay humanizado previo.

        Se registra como accion CAST en el log.
        El parametro ``_hwnd`` se acepta por compatibilidad pero se ignora.

        Returns:
            True si la tecla se envio correctamente.
        """
        self._human_delay()
        return self._press_key("CAST")

    def send_key_no_delay(self, _hwnd: object = None) -> bool:
        """Envia la tecla configurada SIN delay humanizado.

        Se registra como accion LOOT en el log.
        El parametro ``_hwnd`` se acepta por compatibilidad pero se ignora.

        Returns:
            True si la tecla se envio correctamente.
        """
        return self._press_key("LOOT")
