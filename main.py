"""
main.py — Punto de entrada del bot de pesca de WoW.

Gestiona la carga de configuración, auto-instalación de dependencias,
selección de dispositivo de audio, y el bucle principal de pesca.
"""

from __future__ import annotations

import json
import logging
import os
import random
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Auto-instalación de dependencias
# ---------------------------------------------------------------------------

REQUIRED_PACKAGES: dict[str, str] = {
    "sounddevice": "sounddevice",
    "numpy": "numpy",
    "pydirectinput": "pydirectinput",
    "win32gui": "pywin32",
    "rich": "rich",
}


def _ensure_package(import_name: str, pip_name: str) -> None:
    """Instala un paquete si no está disponible.

    Args:
        import_name: Nombre del módulo para importar.
        pip_name: Nombre del paquete en pip.
    """
    try:
        __import__(import_name)
    except ImportError:
        print(f"[AUTO-INSTALL] Instalando {pip_name}...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", pip_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def auto_install_dependencies() -> None:
    """Verifica e instala automáticamente todas las dependencias necesarias."""
    for import_name, pip_name in REQUIRED_PACKAGES.items():
        _ensure_package(import_name, pip_name)


# Instalar dependencias antes de importar módulos del proyecto
auto_install_dependencies()

# Ahora podemos importar de forma segura
from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.table import Table  # noqa: E402
from rich.text import Text  # noqa: E402

from audio_listener import AudioListener  # noqa: E402
from input_handler import InputHandler  # noqa: E402
from session_tracker import SessionTracker  # noqa: E402
from window_watcher import WindowWatcher  # noqa: E402

# ---------------------------------------------------------------------------
# Constantes y paths
# ---------------------------------------------------------------------------

BASE_DIR: Path = Path(__file__).resolve().parent
CONFIG_PATH: Path = BASE_DIR / "config.json"
LOG_DIR: Path = BASE_DIR / "logs"
LOG_FILE: Path = LOG_DIR / "fishing_bot.log"

console = Console()

# ---------------------------------------------------------------------------
# Configuración por defecto
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: dict[str, Any] = {
    "keybind": "`",
    "iterations": 1000,
    "cast_animation_wait": 1.5,
    "cast_animation_variance": 0.3,
    "post_loot_wait": 1.0,
    "post_loot_variance": 0.2,
    "detection_timeout_seconds": 30,
    "post_detection_cooldown": 3.0,
    "min_human_delay": 0.05,
    "max_human_delay": 0.30,
    "rms_threshold": 0.02,
    "audio_device_index": 41,
    "sample_rate": 44100,
    "block_size": 1024,
    "log_level": "INFO",
    "afk_break_enabled": True,
    "afk_break_every_n_iterations": 50,
    "afk_break_duration_seconds": 20,
    "cast_threshold_factor": 300.0,
    "bite_threshold_factor": 500.0,
    "min_absolute_threshold": 0.005,
    "ignore_after_cast_seconds": 0.5,
    "bite_detection_timeout_secs": 20.0,
}


# ---------------------------------------------------------------------------
# Funciones de configuración y logging
# ---------------------------------------------------------------------------


def load_config() -> dict[str, Any]:
    """Carga la configuración desde config.json, creándolo con defaults si no existe.

    Returns:
        Diccionario con la configuración cargada.
    """
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user_config: dict[str, Any] = json.load(f)
            # Merge con defaults para campos faltantes
            merged = {**DEFAULT_CONFIG, **user_config}
            # Guardar merge para que el usuario vea todos los campos
            save_config(merged)
            return merged
        except (json.JSONDecodeError, IOError) as exc:
            console.print(
                f"[bold red]Error leyendo config.json: {exc}[/bold red]"
            )
            console.print("[yellow]Usando configuración por defecto.[/yellow]")
            save_config(DEFAULT_CONFIG)
            return dict(DEFAULT_CONFIG)
    else:
        console.print(
            "[yellow]config.json no encontrado — creando con valores por defecto.[/yellow]"
        )
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)


def save_config(config: dict[str, Any]) -> None:
    """Guarda la configuración en config.json.

    Args:
        config: Diccionario de configuración a guardar.
    """
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def setup_logging(log_level: str = "INFO") -> None:
    """Configura el sistema de logging con archivo rotativo y salida rich.

    Args:
        log_level: Nivel de logging (DEBUG, INFO, WARNING, ERROR, CRITICAL).
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Formato para archivo
    file_formatter = logging.Formatter(
        "%(asctime)s | %(name)-30s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Handler de archivo rotativo (5 MB max, 3 backups)
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(logging.DEBUG)  # Archivo siempre en DEBUG

    # Handler de consola simplificado (rich maneja el formato)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(
        logging.Formatter("%(levelname)-8s | %(message)s")
    )
    console_handler.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Configurar root logger del bot
    root_logger = logging.getLogger("fishing_bot")
    root_logger.setLevel(logging.DEBUG)
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


# ---------------------------------------------------------------------------
# Selección de dispositivo de audio
# ---------------------------------------------------------------------------


def select_audio_device(config: dict[str, Any]) -> int:
    """Selecciona el dispositivo de audio para captura loopback.

    Si audio_device_index está configurado y es válido, lo usa directamente.
    Si no, intenta auto-detectar un dispositivo loopback.
    Si no encuentra nada, lista los dispositivos y permite al usuario elegir.

    Args:
        config: Diccionario de configuración actual.

    Returns:
        Índice del dispositivo de audio seleccionado.

    Raises:
        SystemExit: Si no se puede encontrar o seleccionar un dispositivo.
    """
    # Verificar si ya está configurado
    if config.get("audio_device_index") is not None:
        device_idx = config["audio_device_index"]
        console.print(
            f"[green]Usando dispositivo de audio configurado: índice {device_idx}[/green]"
        )
        return device_idx

    # Intentar auto-detección
    console.print("[cyan]Buscando dispositivo de audio loopback...[/cyan]")
    auto_idx = AudioListener.find_loopback_device()

    if auto_idx is not None:
        console.print(
            f"[green]Dispositivo loopback auto-detectado: índice {auto_idx}[/green]"
        )
        config["audio_device_index"] = auto_idx
        save_config(config)
        return auto_idx

    # Listar dispositivos y dejar que el usuario elija
    devices = AudioListener.list_loopback_devices()

    if not devices:
        _print_no_audio_guide()
        sys.exit(1)

    console.print("\n[bold yellow]No se encontró dispositivo loopback automáticamente.[/bold yellow]")
    console.print("[cyan]Dispositivos de entrada disponibles:[/cyan]\n")

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Índice", style="cyan", justify="right")
    table.add_column("Nombre", style="green")
    table.add_column("Canales", justify="center")
    table.add_column("Sample Rate", justify="right")

    for dev in devices:
        table.add_row(
            str(dev["index"]),
            dev["name"],
            str(dev["channels"]),
            f"{dev['samplerate']:.0f} Hz",
        )

    console.print(table)

    while True:
        try:
            choice = input("\nSelecciona el índice del dispositivo: ").strip()
            choice_int = int(choice)
            valid_indices = [d["index"] for d in devices]
            if choice_int in valid_indices:
                config["audio_device_index"] = choice_int
                save_config(config)
                console.print(
                    f"[green]Dispositivo seleccionado: {choice_int} — guardado en config.json[/green]"
                )
                return choice_int
            else:
                console.print("[red]Índice no válido. Intenta de nuevo.[/red]")
        except (ValueError, EOFError):
            console.print("[red]Entrada no válida. Introduce un número.[/red]")


def _print_no_audio_guide() -> None:
    """Imprime una guía paso a paso para habilitar Stereo Mix en Windows."""
    guide = """
[bold red]ERROR: No se encontró ningún dispositivo de entrada de audio.[/bold red]

Para que el bot funcione, necesitas habilitar [bold]Stereo Mix[/bold] o un
dispositivo de [bold]loopback[/bold] en Windows:

[bold cyan]Pasos para habilitar Stereo Mix:[/bold cyan]

  1. Clic derecho en el icono de volumen en la barra de tareas
  2. Selecciona [bold]"Configuración de sonido"[/bold]
  3. Desplázate hasta [bold]"Más opciones de sonido"[/bold] (o Panel de Control > Sonido)
  4. Ve a la pestaña [bold]"Grabación"[/bold]
  5. Clic derecho en un área vacía → marca [bold]"Mostrar dispositivos deshabilitados"[/bold]
  6. Si ves [bold]"Stereo Mix"[/bold], clic derecho → [bold]"Habilitar"[/bold]
  7. Clic derecho → [bold]"Establecer como dispositivo predeterminado"[/bold]

[bold yellow]Nota:[/bold yellow] Si no aparece Stereo Mix, tu tarjeta de sonido puede no soportarlo.
Alternativa: instala [bold]VB-Audio Virtual Cable[/bold] (gratuito) como dispositivo loopback.

Tras habilitar el dispositivo, vuelve a ejecutar el bot.
"""
    console.print(Panel(guide, title="Guía de Configuración de Audio", border_style="red"))


# ---------------------------------------------------------------------------
# Banner de bienvenida
# ---------------------------------------------------------------------------


def print_welcome_banner(config: dict[str, Any]) -> None:
    """Imprime el banner de bienvenida con resumen de configuración.

    Args:
        config: Diccionario de configuración actual.
    """
    title = Text("WoW Fishing Bot", style="bold bright_cyan")
    subtitle = Text("Better Fishing + Speedy AutoLoot", style="dim")

    banner_text = Text()
    banner_text.append(title)
    banner_text.append("\n")
    banner_text.append(subtitle)

    console.print()
    console.print(
        Panel(
            banner_text,
            border_style="bright_cyan",
            padding=(1, 4),
        )
    )

    # Tabla de configuración
    table = Table(
        title="[bold]Configuración Activa[/bold]",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Parámetro", style="cyan", min_width=30)
    table.add_column("Valor", style="green", justify="right", min_width=15)

    table.add_row("Tecla (keybind)", config["keybind"])
    table.add_row("Iteraciones", str(config["iterations"]))
    table.add_row("Espera animación cast", f'{config["cast_animation_wait"]}s ±{config["cast_animation_variance"]}s')
    table.add_row("Espera post-loot", f'{config["post_loot_wait"]}s ±{config["post_loot_variance"]}s')
    table.add_row("Timeout detección", f'{config["detection_timeout_seconds"]}s')
    table.add_row("Cooldown post-detección", f'{config["post_detection_cooldown"]}s')
    table.add_row("Delay humano", f'{config["min_human_delay"]}s–{config["max_human_delay"]}s')
    table.add_row("Dispositivo audio", str(config["audio_device_index"]))
    table.add_row("Sample rate", f'{config["sample_rate"]} Hz')
    table.add_row("Block size", str(config["block_size"]))
    table.add_row("Cast threshold factor", str(config.get("cast_threshold_factor", 300.0)))
    table.add_row("Bite threshold factor", str(config.get("bite_threshold_factor", 500.0)))
    table.add_row("Min absolute threshold", str(config.get("min_absolute_threshold", 0.005)))
    table.add_row("Ignore after cast", f'{config.get("ignore_after_cast_seconds", 0.5)}s')
    table.add_row("Bite detection timeout", f'{config.get("bite_detection_timeout_secs", 20.0)}s')
    table.add_row("Pausas AFK", f'Cada {config["afk_break_every_n_iterations"]} iter, {config["afk_break_duration_seconds"]}s' if config["afk_break_enabled"] else "Desactivadas")

    console.print(table)

    # Recordatorios
    console.print()
    console.print("[bold yellow]Recordatorios:[/bold yellow]")
    console.print("  • Stereo Mix debe estar habilitado en Configuración de Sonido de Windows")
    console.print("  • El addon [bold]Better Fishing[/bold] debe estar instalado en WoW")
    console.print(f'  • La tecla de pesca debe estar asignada a [bold]"{config["keybind"]}"[/bold]')
    console.print("  • [bold]Speedy AutoLoot[/bold] debe estar activo para looteo automático")
    console.print()


# ---------------------------------------------------------------------------
# Bucle principal de pesca
# ---------------------------------------------------------------------------


def fishing_loop(
    config: dict[str, Any],
    audio: AudioListener,
    input_handler: InputHandler,
    watcher: WindowWatcher,
    tracker: SessionTracker,
) -> None:
    """Ejecuta el bucle principal de pesca con modelo de dos picos.

    Cada iteración:
      1. Cast (envía tecla)
      2. Espera animación de cast
      3. Escucha audio: ignora primer pico (cast splash), espera segundo pico (bite)
      4. Si bite detectado → delay humano → loot (envía tecla)
      5. Espera post-loot

    Args:
        config: Configuración del bot.
        audio: Listener de audio para detección de cast/bite.
        input_handler: Handler de input para enviar teclas.
        watcher: Watcher de ventana de WoW.
        tracker: Tracker de estadísticas de sesión.
    """
    logger = logging.getLogger("fishing_bot.main")
    total_iterations: int = config["iterations"]
    cast_wait: float = config["cast_animation_wait"]
    cast_variance: float = config["cast_animation_variance"]
    post_loot_wait: float = config["post_loot_wait"]
    post_loot_variance: float = config["post_loot_variance"]
    bite_timeout: float = config.get("bite_detection_timeout_secs", 20.0)
    min_delay: float = config["min_human_delay"]
    max_delay: float = config["max_human_delay"]

    tracker.start_session()

    for iteration in range(1, total_iterations + 1):
        console.print(
            f"\n[bold bright_cyan]━━━ Iteración {iteration}/{total_iterations} ━━━[/bold bright_cyan]"
        )

        # 1. Verificar que WoW está en primer plano
        watcher.wait_for_wow_focus()

        # Obtener HWND para fallback PostMessage
        wow_hwnd = watcher.get_wow_hwnd()

        # 2. Enviar tecla para lanzar caña
        console.print("[cyan]  🎣 Lanzando caña...[/cyan]")
        logger.debug("Cast #%d — enviando tecla.", iteration)
        if not input_handler.send_key(wow_hwnd):
            logger.error("No se pudo enviar tecla de cast. Reintentando iteración...")
            time.sleep(1.0)
            continue

        # 3. Esperar animación de cast (aleatorizado)
        wait_time = cast_wait + random.uniform(-cast_variance, cast_variance)
        wait_time = max(0.5, wait_time)  # Mínimo 0.5s
        console.print(f"[dim]  Esperando animación de cast ({wait_time:.2f}s)...[/dim]")
        time.sleep(wait_time)

        # 4. Detección de dos picos: cast splash → ignore → bite
        #    (con posibilidad de recast por timeout)
        bite_detected = False
        recast_count = 0
        max_recasts = 3

        while not bite_detected and recast_count <= max_recasts:
            console.print(
                "[yellow]  👂 Escuchando audio (cast splash → ignore → bite)...[/yellow]"
            )
            bite_detected = audio.wait_for_cast_and_bite(bite_timeout=bite_timeout)

            if not bite_detected:
                recast_count += 1
                tracker.record_timeout_recast()
                console.print(
                    f"[red]  ⏰ Timeout sin bite — recast #{recast_count}[/red]"
                )

                if recast_count > max_recasts:
                    console.print("[red]  Máximo de recasts alcanzado. Siguiente iteración.[/red]")
                    break

                # Verificar foco antes de recast
                watcher.wait_for_wow_focus()
                wow_hwnd = watcher.get_wow_hwnd()

                console.print("[cyan]  🎣 Re-lanzando caña...[/cyan]")
                logger.debug("Recast #%d en iteración %d.", recast_count, iteration)
                if not input_handler.send_key(wow_hwnd):
                    logger.error("No se pudo enviar tecla de recast.")
                    break

                wait_time = cast_wait + random.uniform(-cast_variance, cast_variance)
                wait_time = max(0.5, wait_time)
                time.sleep(wait_time)

        if bite_detected:
            # 5. Delay humano antes de lootear
            human_wait = random.uniform(min_delay, max_delay)
            console.print(f"[dim]  Delay humano ({human_wait:.3f}s)...[/dim]")
            time.sleep(human_wait)

            # 6. Verificar foco y enviar tecla para lootear
            if watcher.is_wow_foreground():
                console.print("[green]  💰 ¡Bite! Looteando...[/green]")
                input_handler.send_key_no_delay(wow_hwnd)
                tracker.record_fish()
            else:
                console.print("[red]  WoW perdió el foco antes del loot.[/red]")

        # 7. Espera post-loot
        post_wait = post_loot_wait + random.uniform(-post_loot_variance, post_loot_variance)
        post_wait = max(0.3, post_wait)
        time.sleep(post_wait)

        # Registrar iteración
        tracker.record_iteration()

        # Mostrar stats periódicas
        if tracker.should_show_stats():
            tracker.show_stats()

        # Pausa AFK si toca
        if tracker.should_take_afk_break():
            tracker.take_afk_break()
            # Después de la pausa, esperar a que WoW tenga foco
            watcher.wait_for_wow_focus()


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------


def main() -> None:
    """Punto de entrada principal del bot de pesca."""
    try:
        # Cargar configuración
        config = load_config()

        # Configurar logging
        setup_logging(config.get("log_level", "INFO"))
        logger = logging.getLogger("fishing_bot.main")

        # Banner de bienvenida
        print_welcome_banner(config)

        # Seleccionar dispositivo de audio
        device_index = select_audio_device(config)

        # Inicializar componentes
        audio = AudioListener(
            device_index=device_index,
            sample_rate=config["sample_rate"],
            block_size=config["block_size"],
            rms_threshold=config["rms_threshold"],
            post_detection_cooldown=config["post_detection_cooldown"],
            cast_threshold_factor=config.get("cast_threshold_factor", 300.0),
            bite_threshold_factor=config.get("bite_threshold_factor", 500.0),
            min_absolute_threshold=config.get("min_absolute_threshold", 0.005),
            ignore_after_cast_seconds=config.get("ignore_after_cast_seconds", 0.5),
            bite_detection_timeout_secs=config.get("bite_detection_timeout_secs", 20.0),
        )

        input_handler = InputHandler(
            keybind=config["keybind"],
            min_human_delay=config["min_human_delay"],
            max_human_delay=config["max_human_delay"],
        )

        watcher = WindowWatcher()
        tracker = SessionTracker(
            afk_break_enabled=config["afk_break_enabled"],
            afk_break_every_n=config["afk_break_every_n_iterations"],
            afk_break_duration=config["afk_break_duration_seconds"],
            display_interval=10,
        )

        # Esperar ventana de WoW
        console.print(
            "[bold yellow]Esperando ventana de World of Warcraft...[/bold yellow]"
        )
        if not watcher.wait_for_wow_window(timeout=60.0):
            console.print(
                "[bold red]No se encontró la ventana de WoW tras 60 segundos.[/bold red]"
            )
            console.print("[yellow]Asegúrate de que World of Warcraft está abierto.[/yellow]")
            sys.exit(1)

        # Iniciar stream de audio
        console.print("[cyan]Iniciando captura de audio...[/cyan]")
        audio.start_stream()

        # Calibrar línea base
        console.print("[cyan]Calibrando audio ambiental...[/cyan]")
        audio.calibrate_baseline(duration=2.0)

        # Esperar foco
        console.print(
            "\n[bold green]¡Todo listo! Cambia a la ventana de WoW para comenzar.[/bold green]\n"
        )
        watcher.wait_for_wow_focus()

        # Ejecutar bucle principal
        fishing_loop(config, audio, input_handler, watcher, tracker)

        # Estadísticas finales
        tracker.show_final_stats()

    except KeyboardInterrupt:
        console.print("\n[bold red]Ctrl+C detectado — deteniendo bot...[/bold red]")
        try:
            tracker.show_final_stats()  # type: ignore[possibly-undefined]
        except Exception:
            pass
    except Exception as exc:
        # Log completo al archivo
        logging.getLogger("fishing_bot.main").critical(
            "Error fatal no manejado", exc_info=True
        )
        console.print(f"\n[bold red]Error fatal: {exc}[/bold red]")
        console.print("[yellow]Revisa logs/fishing_bot.log para más detalles.[/yellow]")
        sys.exit(1)
    finally:
        # Cleanup
        try:
            audio.stop_stream()  # type: ignore[possibly-undefined]
        except Exception:
            pass


if __name__ == "__main__":
    main()
