"""
session_tracker.py — Estadísticas de sesión y sistema de pausas AFK.

Registra iteraciones, splashes detectados, recasts por timeout, duración
de sesión y ritmo de pesca/hora. Gestiona las pausas AFK periódicas para
simular comportamiento humano.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from rich.console import Console
from rich.table import Table

logger = logging.getLogger("fishing_bot.session")
console = Console()


class SessionTracker:
    """Rastrea estadísticas de sesión de pesca y gestiona pausas AFK."""

    def __init__(
        self,
        afk_break_enabled: bool = True,
        afk_break_every_n: int = 50,
        afk_break_duration: float = 20.0,
        display_interval: int = 10,
    ) -> None:
        """Inicializa el tracker de sesión.

        Args:
            afk_break_enabled: Si las pausas AFK están activadas.
            afk_break_every_n: Cada cuántas iteraciones hacer pausa.
            afk_break_duration: Duración de la pausa AFK en segundos.
            display_interval: Cada cuántos peces mostrar estadísticas.
        """
        self.afk_break_enabled: bool = afk_break_enabled
        self.afk_break_every_n: int = afk_break_every_n
        self.afk_break_duration: float = afk_break_duration
        self.display_interval: int = display_interval

        self._start_time: Optional[float] = None
        self._iterations_completed: int = 0
        self._fish_caught: int = 0
        self._timeout_recasts: int = 0
        self._total_afk_time: float = 0.0

    def start_session(self) -> None:
        """Marca el inicio de la sesión de pesca."""
        self._start_time = time.time()
        self._iterations_completed = 0
        self._fish_caught = 0
        self._timeout_recasts = 0
        self._total_afk_time = 0.0
        logger.info("Sesión de pesca iniciada.")

    def record_fish(self) -> None:
        """Registra un pez capturado exitosamente."""
        self._fish_caught += 1
        logger.debug("Pez #%d capturado.", self._fish_caught)

    def record_timeout_recast(self) -> None:
        """Registra un recast por timeout de detección."""
        self._timeout_recasts += 1
        logger.debug("Recast por timeout #%d.", self._timeout_recasts)

    def record_iteration(self) -> None:
        """Registra una iteración completada."""
        self._iterations_completed += 1

    @property
    def iterations_completed(self) -> int:
        """Número de iteraciones completadas.

        Returns:
            Total de iteraciones.
        """
        return self._iterations_completed

    @property
    def fish_caught(self) -> int:
        """Número estimado de peces capturados.

        Returns:
            Total de peces.
        """
        return self._fish_caught

    @property
    def session_duration(self) -> float:
        """Duración de la sesión en segundos.

        Returns:
            Segundos desde el inicio, o 0 si no ha iniciado.
        """
        if self._start_time is None:
            return 0.0
        return time.time() - self._start_time

    @property
    def fish_per_hour(self) -> float:
        """Calcula el ritmo de peces por hora.

        Returns:
            Peces por hora estimados.
        """
        duration_hours = self.session_duration / 3600.0
        if duration_hours <= 0:
            return 0.0
        return self._fish_caught / duration_hours

    def _format_duration(self, seconds: float) -> str:
        """Formatea una duración en segundos a formato legible HH:MM:SS.

        Args:
            seconds: Duración en segundos.

        Returns:
            String formateado.
        """
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def should_show_stats(self) -> bool:
        """Verifica si se deben mostrar estadísticas periódicas.

        Returns:
            True si es momento de mostrar stats (cada display_interval peces).
        """
        return (
            self._fish_caught > 0
            and self._fish_caught % self.display_interval == 0
        )

    def show_stats(self) -> None:
        """Muestra las estadísticas actuales en consola con formato rich."""
        table = Table(
            title="[bold cyan]Estadísticas de Pesca[/bold cyan]",
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("Métrica", style="cyan", min_width=25)
        table.add_column("Valor", style="green", justify="right", min_width=15)

        table.add_row("Iteraciones completadas", str(self._iterations_completed))
        table.add_row("Peces capturados", str(self._fish_caught))
        table.add_row("Recasts por timeout", str(self._timeout_recasts))
        table.add_row("Duración de sesión", self._format_duration(self.session_duration))
        table.add_row("Peces/hora", f"{self.fish_per_hour:.1f}")
        table.add_row("Tiempo AFK total", self._format_duration(self._total_afk_time))

        console.print()
        console.print(table)
        console.print()

    def show_final_stats(self) -> None:
        """Muestra las estadísticas finales de sesión con formato destacado."""
        console.print()
        console.rule("[bold red]Sesión de Pesca Finalizada[/bold red]")

        table = Table(
            title="[bold yellow]Resumen Final[/bold yellow]",
            show_header=True,
            header_style="bold magenta",
            border_style="bright_yellow",
        )
        table.add_column("Métrica", style="cyan", min_width=30)
        table.add_column("Valor", style="bold green", justify="right", min_width=15)

        table.add_row("Total iteraciones", str(self._iterations_completed))
        table.add_row("Total peces capturados", str(self._fish_caught))
        table.add_row("Recasts por timeout", str(self._timeout_recasts))
        table.add_row("Duración total", self._format_duration(self.session_duration))
        table.add_row("Peces por hora", f"{self.fish_per_hour:.1f}")
        table.add_row("Tiempo AFK acumulado", self._format_duration(self._total_afk_time))

        success_rate = 0.0
        total_attempts = self._fish_caught + self._timeout_recasts
        if total_attempts > 0:
            success_rate = (self._fish_caught / total_attempts) * 100.0
        table.add_row("Tasa de éxito", f"{success_rate:.1f}%")

        console.print(table)
        console.rule()
        console.print()

    def should_take_afk_break(self) -> bool:
        """Verifica si toca una pausa AFK.

        Returns:
            True si se debe hacer pausa.
        """
        if not self.afk_break_enabled:
            return False
        if self.afk_break_every_n <= 0:
            return False
        return (
            self._iterations_completed > 0
            and self._iterations_completed % self.afk_break_every_n == 0
        )

    def take_afk_break(self) -> None:
        """Ejecuta una pausa AFK con cuenta regresiva en consola."""
        duration = self.afk_break_duration
        console.print(
            f"\n[bold yellow]⏸  Pausa AFK — {duration:.0f} segundos "
            f"(iteración #{self._iterations_completed})[/bold yellow]"
        )

        start = time.time()
        remaining = duration

        while remaining > 0:
            console.print(
                f"  [dim]Reanudando en {remaining:.0f}s...[/dim]",
                end="\r",
            )
            time.sleep(min(1.0, remaining))
            remaining = duration - (time.time() - start)

        self._total_afk_time += duration
        console.print(
            "[bold green]▶  Reanudando pesca...[/bold green]              "
        )
