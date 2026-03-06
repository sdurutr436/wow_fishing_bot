"""
main.py -- Punto de entrada del bot de pesca de WoW.

Gestiona la carga de configuracion, auto-instalacion de dependencias,
seleccion de dispositivo de audio, y el bucle principal de pesca.
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
# Auto-instalacion de dependencias
# ---------------------------------------------------------------------------

REQUIRED_PACKAGES: dict[str, str] = {
    "sounddevice": "sounddevice",
    "numpy": "numpy",
    "pydirectinput": "pydirectinput",
    "win32gui": "pywin32",
    "rich": "rich",
}


def _ensure_package(import_name: str, pip_name: str) -> None:
    """Instala un paquete si no esta disponible."""
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
    """Verifica e instala automaticamente todas las dependencias necesarias."""
    for import_name, pip_name in REQUIRED_PACKAGES.items():
        _ensure_package(import_name, pip_name)


# Instalar dependencias antes de importar modulos del proyecto
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
# Configuracion por defecto
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: dict[str, Any] = {
    "keybind": ".",
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
    "reset_factor": 2.0,
    "iterations_before_cleanup": 40,
    "calibration_duration_seconds": 2.0,
}


# ---------------------------------------------------------------------------
# Funciones de configuracion y logging
# ---------------------------------------------------------------------------


def load_config() -> dict[str, Any]:
    """Carga la configuracion desde config.json, creandolo con defaults si no existe."""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user_config: dict[str, Any] = json.load(f)
            merged = {**DEFAULT_CONFIG, **user_config}
            save_config(merged)
            return merged
        except (json.JSONDecodeError, IOError) as exc:
            console.print(
                f"[bold red]Error leyendo config.json: {exc}[/bold red]"
            )
            console.print("[yellow]Usando configuracion por defecto.[/yellow]")
            save_config(DEFAULT_CONFIG)
            return dict(DEFAULT_CONFIG)
    else:
        console.print(
            "[yellow]config.json no encontrado -- creando con valores por defecto.[/yellow]"
        )
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)


def save_config(config: dict[str, Any]) -> None:
    """Guarda la configuracion en config.json."""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def setup_logging(log_level: str = "INFO") -> None:
    """Configura el sistema de logging con archivo rotativo y salida rich."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    file_formatter = logging.Formatter(
        "%(asctime)s | %(name)-30s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(
        logging.Formatter("%(levelname)-8s | %(message)s")
    )
    console_handler.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    root_logger = logging.getLogger("fishing_bot")
    root_logger.setLevel(logging.DEBUG)
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


# ---------------------------------------------------------------------------
# Seleccion de dispositivo de audio
# ---------------------------------------------------------------------------


def select_audio_device(config: dict[str, Any]) -> int:
    """Selecciona el dispositivo de audio para captura loopback."""
    if config.get("audio_device_index") is not None:
        device_idx = config["audio_device_index"]
        console.print(
            f"[green]Usando dispositivo de audio configurado: indice {device_idx}[/green]"
        )
        return device_idx

    console.print("[cyan]Buscando dispositivo de audio loopback...[/cyan]")
    auto_idx = AudioListener.find_loopback_device()

    if auto_idx is not None:
        console.print(
            f"[green]Dispositivo loopback auto-detectado: indice {auto_idx}[/green]"
        )
        config["audio_device_index"] = auto_idx
        save_config(config)
        return auto_idx

    devices = AudioListener.list_loopback_devices()

    if not devices:
        _print_no_audio_guide()
        sys.exit(1)

    console.print("\n[bold yellow]No se encontro dispositivo loopback automaticamente.[/bold yellow]")
    console.print("[cyan]Dispositivos de entrada disponibles:[/cyan]\n")

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Indice", style="cyan", justify="right")
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
            choice = input("\nSelecciona el indice del dispositivo: ").strip()
            choice_int = int(choice)
            valid_indices = [d["index"] for d in devices]
            if choice_int in valid_indices:
                config["audio_device_index"] = choice_int
                save_config(config)
                console.print(
                    f"[green]Dispositivo seleccionado: {choice_int} -- guardado en config.json[/green]"
                )
                return choice_int
            else:
                console.print("[red]Indice no valido. Intenta de nuevo.[/red]")
        except (ValueError, EOFError):
            console.print("[red]Entrada no valida. Introduce un numero.[/red]")


def _print_no_audio_guide() -> None:
    """Imprime una guia paso a paso para habilitar Stereo Mix en Windows."""
    guide = """
[bold red]ERROR: No se encontro ningun dispositivo de entrada de audio.[/bold red]

Para que el bot funcione, necesitas habilitar [bold]Stereo Mix[/bold] o un
dispositivo de [bold]loopback[/bold] en Windows:

[bold cyan]Pasos para habilitar Stereo Mix:[/bold cyan]

  1. Clic derecho en el icono de volumen en la barra de tareas
  2. Selecciona [bold]"Configuracion de sonido"[/bold]
  3. Desplazate hasta [bold]"Mas opciones de sonido"[/bold] (o Panel de Control > Sonido)
  4. Ve a la pestana [bold]"Grabacion"[/bold]
  5. Clic derecho en un area vacia -> marca [bold]"Mostrar dispositivos deshabilitados"[/bold]
  6. Si ves [bold]"Stereo Mix"[/bold], clic derecho -> [bold]"Habilitar"[/bold]
  7. Clic derecho -> [bold]"Establecer como dispositivo predeterminado"[/bold]

[bold yellow]Nota:[/bold yellow] Si no aparece Stereo Mix, tu tarjeta de sonido puede no soportarlo.
Alternativa: instala [bold]VB-Audio Virtual Cable[/bold] (gratuito) como dispositivo loopback.

Tras habilitar el dispositivo, vuelve a ejecutar el bot.
"""
    console.print(Panel(guide, title="Guia de Configuracion de Audio", border_style="red"))


# ---------------------------------------------------------------------------
# Banner de bienvenida
# ---------------------------------------------------------------------------


def print_welcome_banner(config: dict[str, Any]) -> None:
    """Imprime el banner de bienvenida con resumen de configuracion."""
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

    table = Table(
        title="[bold]Configuracion Activa[/bold]",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Parametro", style="cyan", min_width=30)
    table.add_column("Valor", style="green", justify="right", min_width=15)

    table.add_row("Tecla (keybind)", config["keybind"])
    table.add_row("Metodo de input", "pydirectinput.press() UNICAMENTE")
    table.add_row("Iteraciones", str(config["iterations"]))
    table.add_row("Espera animacion cast", f'{config["cast_animation_wait"]}s +/-{config["cast_animation_variance"]}s')
    table.add_row("Espera post-loot", f'{config["post_loot_wait"]}s +/-{config["post_loot_variance"]}s')
    table.add_row("Timeout deteccion", f'{config["detection_timeout_seconds"]}s')
    table.add_row("Cooldown post-deteccion", f'{config["post_detection_cooldown"]}s')
    table.add_row("Delay humano", f'{config["min_human_delay"]}s-{config["max_human_delay"]}s')
    table.add_row("Dispositivo audio", str(config["audio_device_index"]))
    table.add_row("Sample rate", f'{config["sample_rate"]} Hz')
    table.add_row("Block size", str(config["block_size"]))
    table.add_row("Cast threshold factor", str(config.get("cast_threshold_factor", 300.0)))
    table.add_row("Bite threshold factor", str(config.get("bite_threshold_factor", 500.0)))
    table.add_row("Min absolute threshold", str(config.get("min_absolute_threshold", 0.005)))
    table.add_row("Ignore after cast", f'{config.get("ignore_after_cast_seconds", 0.5)}s')
    table.add_row("Bite detection timeout", f'{config.get("bite_detection_timeout_secs", 20.0)}s')
    table.add_row("Reset factor", str(config.get("reset_factor", 2.0)))
    table.add_row("Cleanup cada N iter", str(config.get("iterations_before_cleanup", 40)))
    table.add_row("Duracion calibracion", f'{config.get("calibration_duration_seconds", 2.0)}s')
    table.add_row(
        "Pausas AFK",
        f'Cada {config["afk_break_every_n_iterations"]} iter, {config["afk_break_duration_seconds"]}s'
        if config["afk_break_enabled"]
        else "Desactivadas",
    )

    console.print(table)

    console.print()
    console.print("[bold yellow]Recordatorios:[/bold yellow]")
    console.print("  * Stereo Mix debe estar habilitado en Configuracion de Sonido de Windows")
    console.print("  * El addon [bold]Better Fishing[/bold] debe estar instalado en WoW")
    console.print(f'  * La tecla de pesca debe estar asignada a [bold]"{config["keybind"]}"[/bold]')
    console.print("  * [bold]Speedy AutoLoot[/bold] debe estar activo para looteo automatico")
    console.print("  * Input: SOLO pydirectinput.press() -- sin SendInput/PostMessage")
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
    """Ejecuta el bucle principal de pesca con modelo de dos picos y reset-level.

    Cada iteracion:
      1. Cast (envia tecla via pydirectinput.press)
      2. Espera animacion de cast
      3. Escucha audio: cast splash -> ignore -> reset level -> bite
      4. Si bite detectado -> delay humano -> loot (pydirectinput.press)
      5. Espera post-loot
      6. Cada N iteraciones: limpieza y recalibracion
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
    cleanup_every: int = int(config.get("iterations_before_cleanup", 40))
    calibration_dur: float = config.get("calibration_duration_seconds", 2.0)

    tracker.start_session()

    for iteration in range(1, total_iterations + 1):
        console.print(
            f"\n[bold bright_cyan]--- Iteracion {iteration}/{total_iterations} ---[/bold bright_cyan]"
        )

        # -- Limpieza periodica cada N iteraciones -----------------------
        if cleanup_every > 0 and iteration > 1 and (iteration - 1) % cleanup_every == 0:
            console.print(
                f"[bold magenta]  Limpieza periodica (cada {cleanup_every} iter) "
                f"-- recalibrando audio...[/bold magenta]"
            )
            logger.info(
                "Limpieza periodica en iteracion %d (cada %d iter)",
                iteration,
                cleanup_every,
            )
            new_baseline = audio.reset_cleanup(calibration_duration=calibration_dur)
            console.print(
                f"[magenta]  Nueva baseline: {new_baseline:.8f}[/magenta]"
            )

        # 1. Verificar que WoW esta en primer plano
        watcher.wait_for_wow_focus()

        # 2. Enviar tecla para lanzar cana
        console.print("[cyan]  Lanzando cana...[/cyan]")
        logger.debug("Cast #%d -- enviando tecla.", iteration)
        if not input_handler.send_key():
            logger.error("No se pudo enviar tecla de cast. Reintentando iteracion...")
            time.sleep(1.0)
            continue

        # 3. Esperar animacion de cast (aleatorizado)
        wait_time = cast_wait + random.uniform(-cast_variance, cast_variance)
        wait_time = max(0.5, wait_time)
        console.print(f"[dim]  Esperando animacion de cast ({wait_time:.2f}s)...[/dim]")
        time.sleep(wait_time)

        # 4. Deteccion de dos picos con reset-level
        bite_detected = False
        recast_count = 0
        max_recasts = 3

        while not bite_detected and recast_count <= max_recasts:
            console.print(
                "[yellow]  Escuchando audio (cast splash -> reset -> bite)...[/yellow]"
            )
            bite_detected = audio.wait_for_cast_and_bite(bite_timeout=bite_timeout)

            if not bite_detected:
                recast_count += 1
                tracker.record_timeout_recast()
                console.print(
                    f"[red]  Timeout sin bite -- recast #{recast_count}[/red]"
                )

                if recast_count > max_recasts:
                    console.print("[red]  Maximo de recasts alcanzado. Siguiente iteracion.[/red]")
                    break

                watcher.wait_for_wow_focus()

                console.print("[cyan]  Re-lanzando cana...[/cyan]")
                logger.debug("Recast #%d en iteracion %d.", recast_count, iteration)
                if not input_handler.send_key():
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
                console.print("[green]  Bite! Looteando...[/green]")
                input_handler.send_key_no_delay()
                tracker.record_fish()
            else:
                console.print("[red]  WoW perdio el foco antes del loot.[/red]")

        # 7. Espera post-loot
        post_wait = post_loot_wait + random.uniform(-post_loot_variance, post_loot_variance)
        post_wait = max(0.3, post_wait)
        time.sleep(post_wait)

        # Registrar iteracion
        tracker.record_iteration()

        # Mostrar stats periodicas
        if tracker.should_show_stats():
            tracker.show_stats()

        # Pausa AFK si toca
        if tracker.should_take_afk_break():
            tracker.take_afk_break()
            watcher.wait_for_wow_focus()


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------


def main() -> None:
    """Punto de entrada principal del bot de pesca."""
    try:
        config = load_config()
        setup_logging(config.get("log_level", "INFO"))
        logger = logging.getLogger("fishing_bot.main")

        print_welcome_banner(config)

        device_index = select_audio_device(config)

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
            reset_factor=config.get("reset_factor", 2.0),
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

        console.print(
            "[bold yellow]Esperando ventana de World of Warcraft...[/bold yellow]"
        )
        if not watcher.wait_for_wow_window(timeout=60.0):
            console.print(
                "[bold red]No se encontro la ventana de WoW tras 60 segundos.[/bold red]"
            )
            console.print("[yellow]Asegurate de que World of Warcraft esta abierto.[/yellow]")
            sys.exit(1)

        console.print("[cyan]Iniciando captura de audio...[/cyan]")
        audio.start_stream()

        console.print("[cyan]Calibrando audio ambiental...[/cyan]")
        audio.calibrate_baseline(
            duration=config.get("calibration_duration_seconds", 2.0)
        )

        console.print(
            "\n[bold green]Todo listo! Cambia a la ventana de WoW para comenzar.[/bold green]\n"
        )
        watcher.wait_for_wow_focus()

        fishing_loop(config, audio, input_handler, watcher, tracker)

        tracker.show_final_stats()

    except KeyboardInterrupt:
        console.print("\n[bold red]Ctrl+C detectado -- deteniendo bot...[/bold red]")
        try:
            tracker.show_final_stats()  # type: ignore[possibly-undefined]
        except Exception:
            pass
    except Exception as exc:
        logging.getLogger("fishing_bot.main").critical(
            "Error fatal no manejado", exc_info=True
        )
        console.print(f"\n[bold red]Error fatal: {exc}[/bold red]")
        console.print("[yellow]Revisa logs/fishing_bot.log para mas detalles.[/yellow]")
        sys.exit(1)
    finally:
        try:
            audio.stop_stream()  # type: ignore[possibly-undefined]
        except Exception:
            pass


if __name__ == "__main__":
    main()
