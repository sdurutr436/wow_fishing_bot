"""
input_handler.py — Simulación de input para el bot de pesca.

Envía pulsaciones de tecla al juego usando pydirectinput (DirectInput level)
como método principal, con fallback a pywin32 PostMessage si falla.
Incluye delays humanizados configurables.
"""

from __future__ import annotations

import ctypes
import logging
import random
import time
from typing import Optional

logger = logging.getLogger("fishing_bot.input")

# Constantes de Windows para PostMessage
WM_KEYDOWN: int = 0x0100
WM_KEYUP: int = 0x0101

# Mapeo de teclas a Virtual Key codes
VK_MAP: dict[str, int] = {
    "`": 0xC0,  # VK_OEM_3 — backtick / tilde
    "~": 0xC0,
    "1": 0x31,
    "2": 0x32,
    "3": 0x33,
    "4": 0x34,
    "5": 0x35,
    "6": 0x36,
    "7": 0x37,
    "8": 0x38,
    "9": 0x39,
    "0": 0x30,
}

# Mapeo de teclas a DirectInput scan codes
SCAN_MAP: dict[str, int] = {
    "`": 0x29,  # backtick / tilde scan code
    "~": 0x29,
    "1": 0x02,
    "2": 0x03,
    "3": 0x04,
    "4": 0x05,
    "5": 0x06,
    "6": 0x07,
    "7": 0x08,
    "8": 0x09,
    "9": 0x0A,
    "0": 0x0B,
}


class InputHandler:
    """Gestiona el envío de teclas al juego con métodos primario y fallback."""

    def __init__(
        self,
        keybind: str = "`",
        min_human_delay: float = 0.05,
        max_human_delay: float = 0.30,
    ) -> None:
        """Inicializa el handler de input.

        Args:
            keybind: Tecla a enviar (por defecto backtick).
            min_human_delay: Delay mínimo humanizado en segundos.
            max_human_delay: Delay máximo humanizado en segundos.
        """
        self.keybind: str = keybind
        self.min_human_delay: float = min_human_delay
        self.max_human_delay: float = max_human_delay
        self._use_directinput: bool = True
        self._pydirectinput_available: bool = False

        # Intentar importar pydirectinput
        try:
            import pydirectinput  # noqa: F401

            self._pydirectinput_available = True
            pydirectinput.PAUSE = 0.0  # Desactivar pausa interna
            logger.info("pydirectinput disponible — usando DirectInput como método primario.")
        except ImportError:
            self._pydirectinput_available = False
            self._use_directinput = False
            logger.warning(
                "pydirectinput no disponible — usando pywin32 PostMessage como fallback."
            )

    def _human_delay(self) -> None:
        """Aplica un delay aleatorio humanizado antes de la pulsación."""
        delay = random.uniform(self.min_human_delay, self.max_human_delay)
        logger.debug("Delay humanizado: %.3fs", delay)
        time.sleep(delay)

    def _send_directinput(self) -> bool:
        """Envía la tecla usando pydirectinput (DirectInput level).

        Returns:
            True si la tecla se envió correctamente, False en caso de error.
        """
        if not self._pydirectinput_available:
            return False

        try:
            import pydirectinput

            # pydirectinput.press maneja backtick/grave correctamente
            if self.keybind == "`":
                # Usar SendInput directamente con scan code para backtick
                self._send_scancode_input(SCAN_MAP.get(self.keybind, 0x29))
            else:
                pydirectinput.press(self.keybind)

            logger.debug("Tecla '%s' enviada via DirectInput.", self.keybind)
            return True
        except Exception as exc:
            logger.warning("Error en DirectInput: %s — intentando fallback.", exc)
            return False

    @staticmethod
    def _send_scancode_input(scan_code: int) -> None:
        """Envía una tecla usando SendInput con el scan code DirectInput.

        Args:
            scan_code: El scan code de la tecla a enviar.
        """
        # Estructuras para SendInput
        KEYEVENTF_SCANCODE = 0x0008
        KEYEVENTF_KEYUP = 0x0002
        INPUT_KEYBOARD = 1

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", ctypes.c_ushort),
                ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]

        class INPUT(ctypes.Structure):
            class _INPUT_UNION(ctypes.Union):
                _fields_ = [("ki", KEYBDINPUT)]

            _fields_ = [
                ("type", ctypes.c_ulong),
                ("union", _INPUT_UNION),
            ]

        # Key down
        key_down = INPUT()
        key_down.type = INPUT_KEYBOARD
        key_down.union.ki.wVk = 0
        key_down.union.ki.wScan = scan_code
        key_down.union.ki.dwFlags = KEYEVENTF_SCANCODE
        key_down.union.ki.time = 0
        key_down.union.ki.dwExtraInfo = ctypes.pointer(ctypes.c_ulong(0))

        # Key up
        key_up = INPUT()
        key_up.type = INPUT_KEYBOARD
        key_up.union.ki.wVk = 0
        key_up.union.ki.wScan = scan_code
        key_up.union.ki.dwFlags = KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP
        key_up.union.ki.time = 0
        key_up.union.ki.dwExtraInfo = ctypes.pointer(ctypes.c_ulong(0))

        # Enviar
        ctypes.windll.user32.SendInput(1, ctypes.byref(key_down), ctypes.sizeof(INPUT))
        time.sleep(random.uniform(0.03, 0.08))  # Duración de pulsación humana
        ctypes.windll.user32.SendInput(1, ctypes.byref(key_up), ctypes.sizeof(INPUT))

    def _send_postmessage(self, hwnd: Optional[int] = None) -> bool:
        """Envía la tecla usando pywin32 PostMessage como fallback.

        Args:
            hwnd: Handle de la ventana de WoW. Si es None, busca la ventana.

        Returns:
            True si la tecla se envió correctamente, False en caso de error.
        """
        try:
            import win32api  # noqa: F401
            import win32gui

            if hwnd is None:
                hwnd = self._find_wow_hwnd()

            if hwnd is None or hwnd == 0:
                logger.error("No se encontró la ventana de WoW para PostMessage.")
                return False

            vk_code = VK_MAP.get(self.keybind, 0xC0)

            # Construir lParam para WM_KEYDOWN
            scan_code = SCAN_MAP.get(self.keybind, 0x29)
            lparam_down = (scan_code << 16) | 1  # repeat count = 1
            lparam_up = (scan_code << 16) | 1 | (1 << 30) | (1 << 31)  # key up flags

            win32gui.PostMessage(hwnd, WM_KEYDOWN, vk_code, lparam_down)
            time.sleep(random.uniform(0.03, 0.08))
            win32gui.PostMessage(hwnd, WM_KEYUP, vk_code, lparam_up)

            logger.debug("Tecla '%s' enviada via PostMessage (hwnd=%s).", self.keybind, hwnd)
            return True
        except Exception as exc:
            logger.error("Error en PostMessage: %s", exc)
            return False

    @staticmethod
    def _find_wow_hwnd() -> Optional[int]:
        """Busca el handle de la ventana de World of Warcraft.

        Returns:
            Handle de la ventana o None si no se encuentra.
        """
        try:
            import win32gui

            result: Optional[int] = None

            def enum_callback(hwnd: int, _: None) -> bool:
                nonlocal result
                if win32gui.IsWindowVisible(hwnd):
                    title = win32gui.GetWindowText(hwnd)
                    if "world of warcraft" in title.lower():
                        result = hwnd
                        return False  # Dejar de enumerar
                return True

            try:
                win32gui.EnumWindows(enum_callback, None)
            except Exception:
                pass  # EnumWindows lanza excepción cuando callback retorna False

            return result
        except ImportError:
            logger.error("pywin32 no disponible para buscar ventana.")
            return None

    def send_key(self, hwnd: Optional[int] = None) -> bool:
        """Envía la tecla configurada con delay humanizado.

        Intenta primero con DirectInput, luego con PostMessage como fallback.

        Args:
            hwnd: Handle opcional de la ventana WoW para PostMessage.

        Returns:
            True si la tecla se envió correctamente por cualquier método.
        """
        self._human_delay()

        if self._use_directinput:
            success = self._send_directinput()
            if success:
                return True
            logger.warning("DirectInput falló — cambiando a PostMessage.")
            self._use_directinput = False

        return self._send_postmessage(hwnd)

    def send_key_no_delay(self, hwnd: Optional[int] = None) -> bool:
        """Envía la tecla configurada SIN delay humanizado.

        Útil para el segundo press (loot) donde el delay se aplica externamente.

        Args:
            hwnd: Handle opcional de la ventana WoW para PostMessage.

        Returns:
            True si la tecla se envió correctamente.
        """
        if self._use_directinput:
            success = self._send_directinput()
            if success:
                return True
            self._use_directinput = False

        return self._send_postmessage(hwnd)
