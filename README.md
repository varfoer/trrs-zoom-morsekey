# morsekey

Use a Morse key (straight key or iambic paddle) on a laptop combo jack as a
sidetone source in Zoom, mixed with your voice mic. Linux + PipeWire.

## How it works

The headphone outputs play two inaudible probe tones (L = 17 kHz for dit,
R = 19 kHz for dah). A closed paddle passes its probe through resistors into
the mic input. The program detects the tones per 1 ms window, runs an iambic
keyer (mode A/B) or straight key, and plays a clean 650 Hz sidetone into a
virtual microphone. A lost probe (cable pulled, output muted) reads as key up,
so faults end in silence, never in a stuck tone.

## Wiring (combo jack, CTIA, one TRRS plug)

Paddle:

```
TRRS plug (into laptop)                          key socket (TRS female)
tip    (HP L, dit probe) ──[1k]───────────────── tip    (dit contact)
ring1  (HP R, dah probe) ──[1k]───────────────── ring   (dah contact)
sleeve (mic) ──||──[47k]──────────────────────── sleeve (paddle common)
            100nF
ring2  (GND): plug ground only
```

Straight key (run with `--mode straight`):

```
tip    (HP L, probe) ──[1k]──o/ o──[47k]──||── sleeve (mic)
                             key       100nF
ring1, ring2: not connected
```

- 1 kΩ: isolates the two headphone outputs when both paddles are closed.
- 47 kΩ: attenuates the probe to mic level; lower it (22 kΩ) if the probe is too weak.
- 100 nF (anything from about 1 nF up): keeps the mic bias DC out of the headphone amp.
- If the laptop does not switch to "Headset Mic", add 2.2–4.7 kΩ from mic to GND.

## Install

```
sudo apt install pipewire-jack      # or your distro's equivalent
pip install -r requirements.txt
```

## GUI (recommended)

```
sudo pacman -S tk                   # Tkinter for the venv's Python
./morsekey-gui
```

Pick the devices once; they and all settings are saved to
`~/.config/morsekey/config.json`, and the next launch starts right away.
The GUI creates `zoom_mix`/`zoom_mic` itself and connects devices that show up
later (e.g. Bluetooth headphones switched on after start). On quit it removes
the virtual devices it created, restores auto-mute, and drops all links. If it
ever crashes, the next launch undoes the leftovers first
(`~/.local/state/morsekey/restore.json`).

- *Keep laptop speakers on* disables ALSA auto-mute so you hear the sidetone on
  the built-in speakers with the key plugged in. They also play the 17/19 kHz
  probes (inaudible to most people) and anything else sent to the jack output.
- *Buffer* sets the requested PipeWire buffer; 128 frames is about 2.7 ms.
- Latency you hear on Bluetooth headphones (150–250 ms) comes from Bluetooth
  itself. For a snappy sidetone use the speakers, a USB headset or a USB dongle.

## Command line

```
./setup_audio.sh                    # creates zoom_mix sink + zoom_mic source
pw-jack python3 morsekey.py --list-ports
pw-jack python3 morsekey.py --wpm 22 --voice 'USB.*capture'
```

In Zoom select **zoom_mic** as the microphone. Keep the key up for the first
0.3 s after start: that measures the noise floor. If the key was down, nothing
keys until the floor re-learns after release (a few seconds). The status line
shows each probe's level above the noise floor: about 0 dB open, 30 dB or more
closed. A contact closes above +18 dB and opens below +12 dB.

`./teardown_audio.sh` removes the virtual devices.

Lower latency: `pw-jack -p 128 python3 morsekey.py`.

### Options worth knowing

| Option | Purpose |
| --- | --- |
| `--mode A\|B\|straight` | keyer mode (default B) |
| `--wpm N` | iambic speed |
| `--swap` | swap paddles |
| `--mic`, `--headphones` | regex for ports if the physical defaults are wrong |
| `--voice REGEX` | capture ports mixed into zoom_mix as your voice |
| `--listen REGEX` | also play the sidetone on these playback ports (USB headset, HDMI, ...) |
| `--local-level N` | sidetone level for yourself (combo jack and `--listen`), 0 = off |
| `--dit-freq`, `--dah-freq` | lower the probes (e.g. 12000/14000) if the codec filters 17/19 kHz |

## Zoom settings

Zoom's noise suppression removes steady tones. Set *Settings → Audio →
Background noise suppression* to **Low**, or enable **Original sound for
musicians**.

## Limits

- Combo jack mic and the internal mic usually share one ADC: with the key
  plugged in, the internal mic is gone. Use a USB or Bluetooth mic for voice.
- The headphone output volume scales the probe. Any setting that keeps the
  closed level well above +18 dB works; muting it just stops keying.
- Not usable under WSL2 audio; run on native Linux.

## Tests

```
pytest
```
