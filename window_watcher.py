"""
window_watcher.py — Detección de la ventana de World of Warcraft.

Utiliza pywin32 (win32gui, win32process) para detectar si WoW es la ventana
activa en primer plano. Proporciona métodos para esperar a que WoW se active
y para verificar el estado actual del enfoque.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger("fishing_bot.window")

# Título parcial a buscar (case-insensitive)
WOW_TITLE_MATCH: str = "world of warcraft"


class WindowWatcher:
    """Monitorea la ventana de World of Warcraft y su estado de enfoque."""

    def __init__(self) -> None:
        """Inicializa el watcher de ventanas."""
        self._wow_hwnd: Optional[int] = None
        self._win32gui_available: bool = False

        try:
            import win32gui  # noqa: F401

            self._win32gui_available = True
            logger.info("pywin32 (win32gui) disponible para detección de ventana.")
        except ImportError:
            logger.error(
                "pywin32 no está instalado. "
                "Ejecuta: pip install pywin32"
            )

    def _find_wow_window(self) -> Optional[int]:
        """Busca la ventana de World of Warcraft entre todas las ventanas visibles.

        Returns:
            Handle (HWND) de la ventana de WoW, o None si no se encuentra.
        """
        if not self._win32gui_available:
            return None

        import win32gui

        found_hwnd: Optional[int] = None

        def _enum_callback(hwnd: int, _: None) -> bool:
            """Callback para EnumWindows que busca la ventana de WoW."""
            nonlocal found_hwnd
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if WOW_TITLE_MATCH in title.lower():
                    found_hwnd = hwnd
                    return False  # Detener enumeración
            return True

        try:
            win32gui.EnumWindows(_enum_callback, None)
        except Exception:
            # EnumWindows lanza una excepción pywintypes.error cuando
            # el callback retorna False para detener la enumeración
            pass

        if found_hwnd:
            self._wow_hwnd = found_hwnd

        return found_hwnd

    def is_wow_foreground(self) -> bool:
        """Verifica si World of Warcraft es la ventana activa en primer plano.

        Returns:
            True si WoW está en primer plano, False en caso contrario.
        """
        if not self._win32gui_available:
            logger.warning("win32gui no disponible — asumiendo WoW en primer plano.")
            return True

        import win32gui

        try:
            foreground_hwnd = win32gui.GetForegroundWindow()
            if foreground_hwnd == 0:
                return False

            title = win32gui.GetWindowText(foreground_hwnd)
            is_wow = WOW_TITLE_MATCH in title.lower()

            if is_wow:
                self._wow_hwnd = foreground_hwnd

            return is_wow
        except Exception as exc:
            logger.warning("Error al verificar ventana en primer plano: %s", exc)
            return False

    def get_wow_hwnd(self) -> Optional[int]:
        """Obtiene el handle de la ventana de WoW (cacheado o buscando).

        Returns:
            Handle de la ventana de WoW o None.
        """
        if self._wow_hwnd is not None:
            # Verificar que el handle sigue siendo válido
            if self._win32gui_available:
                import win32gui

                try:
                    if win32gui.IsWindow(self._wow_hwnd):
                        return self._wow_hwnd
                except Exception:
                    pass
            self._wow_hwnd = None

        return self._find_wow_window()

    def wait_for_wow_focus(self, poll_interval: float = 2.0) -> None:
        """Espera bloqueante hasta que WoW sea la ventana activa.

        Imprime un aviso cada vez que detecta que WoW no tiene el foco
        y sondea cada poll_interval segundos.

        Args:
            poll_interval: Intervalo en segundos entre cada verificación.
        """
        if self.is_wow_foreground():
            return

        logger.warning(
            "WoW no está en primer plano. Esperando enfoque... "
            "(cambia a la ventana de WoW)"
        )

        warned = False
        while not self.is_wow_foreground():
            if not warned:
                warned = True
            time.sleep(poll_interval)

        logger.info("WoW detectado en primer plano. Reanudando...")

    def wait_for_wow_window(self, timeout: float = 60.0, poll_interval: float = 2.0) -> bool:
        """Espera a que exista la ventana de WoW (no necesariamente en foco).

        Args:
            timeout: Tiempo máximo de espera en segundos.
            poll_interval: Intervalo entre verificaciones.

        Returns:
            True si se encontró la ventana dentro del timeout, False si no.
        """
        start = time.time()
        logger.info("Buscando ventana de World of Warcraft...")

        while time.time() - start < timeout:
            hwnd = self._find_wow_window()
            if hwnd is not None:
                logger.info(
                    "Ventana de WoW encontrada (HWND=%s).",
                    hwnd,
                )
                return True
            time.sleep(poll_interval)

        logger.error(
            "No se encontró la ventana de WoW tras %.0f segundos. "
            "Asegúrate de que el juego está abierto.",
            timeout,
        )
        return False
