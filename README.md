# WoW Fishing Bot

Bot de pesca automatizado para World of Warcraft. Funciona como un script externo de Python que detecta audio del sistema para automatizar el bucle de pesca.

## Requisitos

- **Python 3.9+** en Windows 10/11
- **World of Warcraft** con los addons:
  - [Better Fishing](https://www.curseforge.com/wow/addons/better-fishing) — vincula cast e interact al mismo botón
  - [Speedy AutoLoot](https://www.curseforge.com/wow/addons/speedyautoloot) — looteo instantáneo automático
- **Stereo Mix** habilitado en Windows (o dispositivo loopback equivalente)

## Instalación Rápida

### Opción 1: Script automático
```bat
install.bat
```

### Opción 2: Manual
```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## Uso

### Opción 1: Script
```bat
run.bat
```

### Opción 2: Manual
```bash
venv\Scripts\activate
python main.py
```

## Configuración

Toda la configuración está en `config.json`. Se crea automáticamente con valores por defecto en la primera ejecución.

| Parámetro | Default | Descripción |
|-----------|---------|-------------|
| `keybind` | `` ` `` | Tecla para cast y loot (debe coincidir con Better Fishing) |
| `iterations` | `1000` | Número total de iteraciones del bucle |
| `cast_animation_wait` | `1.5` | Espera tras el cast (segundos) |
| `cast_animation_variance` | `0.3` | Variación aleatoria del cast wait |
| `post_loot_wait` | `1.0` | Espera tras lootear |
| `post_loot_variance` | `0.2` | Variación aleatoria post-loot |
| `detection_timeout_seconds` | `30` | Timeout de detección de splash |
| `post_detection_cooldown` | `3.0` | Cooldown post-detección (evita ecos) |
| `min_human_delay` | `0.05` | Delay mínimo humanizado |
| `max_human_delay` | `0.30` | Delay máximo humanizado |
| `rms_threshold` | `0.02` | Umbral RMS para detección de splash |
| `audio_device_index` | `null` | Índice del dispositivo (auto-selección si null) |
| `sample_rate` | `44100` | Frecuencia de muestreo |
| `block_size` | `1024` | Tamaño de bloque de audio |
| `log_level` | `INFO` | Nivel de logging |
| `afk_break_enabled` | `true` | Activar pausas AFK |
| `afk_break_every_n_iterations` | `50` | Frecuencia de pausas AFK |
| `afk_break_duration_seconds` | `20` | Duración de pausa AFK |

## Cómo Funciona

```
1. Detecta que WoW es la ventana activa
2. Pulsa la tecla (`) → lanza la caña
3. Espera a que el bobber aterrice en el agua
4. Escucha el audio del sistema buscando el splash
5. Al detectar splash → pulsa la misma tecla (`) → lootea
6. Speedy AutoLoot recoge el loot automáticamente
7. Repite el ciclo
```

## Habilitar Stereo Mix en Windows

1. Clic derecho en el icono de volumen → **Configuración de sonido**
2. **Más opciones de sonido** → pestaña **Grabación**
3. Clic derecho → **Mostrar dispositivos deshabilitados**
4. Habilitar **Stereo Mix** → **Establecer como predeterminado**

Si tu tarjeta no soporta Stereo Mix, instala [VB-Audio Virtual Cable](https://vb-audio.com/Cable/) como alternativa gratuita.

## Estructura del Proyecto

```
wow_fishing_bot/
├── main.py               # Punto de entrada, configuración, bucle principal
├── config.json           # Configuración del usuario
├── audio_listener.py     # Captura de audio y detección de splash
├── input_handler.py      # Simulación de input (DirectInput + PostMessage)
├── window_watcher.py     # Detección de ventana de WoW
├── session_tracker.py    # Estadísticas y pausas AFK
├── requirements.txt      # Dependencias
├── install.bat           # Instalador automático
├── run.bat               # Lanzador
├── logs/                 # Logs rotativos
└── README.md             # Este archivo
```

## Detener el Bot

Pulsa `Ctrl+C` en la consola. El bot mostrará las estadísticas finales de la sesión antes de cerrarse.

## Logs

Los logs se guardan en `logs/fishing_bot.log` con rotación automática (5 MB máx, 3 backups).

## Licencia

Uso personal. Este proyecto es solo para fines educativos.
