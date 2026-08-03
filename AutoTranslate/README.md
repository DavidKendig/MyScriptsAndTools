# AutoTranslate

Batch-translate a folder of images with a local AI model. Point it at a folder,
press Start, and every image gets a `.txt` file beside it with the same name:

```
pages/page1.png   ->  pages/page1.txt
pages/page2.png   ->  pages/page2.txt
pages/page10.png  ->  pages/page10.txt
```

Each page is sent to the model together with **the previous image and the next
image** as context, so names, ongoing sentences and tone stay consistent across
a run. The translation of the previous page is passed along as text too.

Nothing leaves your machine — it talks to **Ollama** or **LM Studio** running
locally.

---

## Install

### Windows

Double-click **`install.bat`**. It finds Python (installing it via winget if
needed), builds a virtual environment in `.venv`, and installs Pillow.

Then double-click **`start.bat`**. `stop.bat` force-closes a run.

### Ubuntu / Debian / Fedora / Arch

```bash
chmod +x install.sh
./install.sh
```

It installs `python3`, `python3-venv`, `python3-tk`, builds the virtual
environment, offers to install Ollama and pull a vision model, and adds
AutoTranslate to your application menu.

Fully unattended:

```bash
./install.sh --yes --with-ollama --model llama3.2-vision
```

Then:

```bash
./start.sh
```

---

## You also need a model server

AutoTranslate is a client. One of these must be running, with a **vision**
model loaded:

| Server | Default URL | Getting a vision model |
|---|---|---|
| [Ollama](https://ollama.com) | `http://localhost:11434` | `ollama pull llama3.2-vision` |
| [LM Studio](https://lmstudio.ai) | `http://localhost:1234/v1` | Load a vision model, then start the local server |

Good vision models: `llama3.2-vision`, `qwen2.5vl`, `minicpm-v`, `gemma3`,
`llava`. A text-only model cannot read images — Ollama reports this, and the
app warns you in the log when you pick one.

---

## Using it

1. **Browse** to your image folder, and the app reports how many it found.
   Files are ordered naturally, so `page2` comes before `page10`.
2. Pick your **server**, press **Refresh** to list the models it has loaded,
   and choose one.
3. Set the **language**, press **Start**.

Leave *Output folder* empty to drop each `.txt` beside its image; set it to
collect them elsewhere (the subfolder layout is mirrored when *Include
subfolders* is on).

### Thinking mode

The **Thinking / reasoning mode** toggle turns the model's reasoning pass on or
off. With Ollama the app asks the server whether the chosen model supports it
and greys the box out when it does not. With LM Studio the switch is sent
anyway, since that server does not advertise the capability.

Reasoning never ends up in your `.txt` files — the toggle only controls whether
the model thinks before answering. Inline `<think>` blocks are always stripped.

### Advanced tab

| Setting | What it does |
|---|---|
| Include previous / following image | Turn both off if your model accepts only one image at a time (most `llava` builds), or if you are short on VRAM |
| Include previous translated text | Carries continuity forward without a second image |
| Max image size | Pages are downscaled before upload. 1400 px suits most comics; raise it for dense small text |
| Context window | Ollama's `num_ctx`. Three images need a large one — raise it if output gets truncated |
| Skip existing | On by default, so an interrupted run resumes where it stopped |
| Retries | Per image, with backoff |

Settings are saved to `config.json` next to the app.

---

## Headless mode

No display needed — handy over SSH:

```bash
./start.sh --cli --folder ~/pages --model llama3.2-vision --language English
```

```
--out DIR         write .txt files here instead of beside the images
--backend NAME    "Ollama" or "LM Studio"
--host URL        override the server URL
--think           enable thinking mode
--recursive       include subfolders
--overwrite       redo pages that already have a .txt
--no-neighbours   send only the current image
```

Exit code is non-zero if any page failed.

---

## Troubleshooting

**"Cannot reach ... Is the server running?"** — start Ollama (`ollama serve`)
or press *Start Server* in LM Studio's developer tab. Check the host URL.

**No models listed** — Ollama needs `ollama pull <model>` first; LM Studio only
lists a model once it is loaded.

**Output is truncated or nonsense** — raise the context window, or turn off the
neighbour images. Small models often cannot handle three images at once.

**The GUI will not open** — install Tkinter (`sudo apt install python3-tk`), or
use `--cli`.

**It is slow** — that is the model, not the app. Lower *Max image size*, turn
thinking off, or use a smaller model.

---

## Layout

```
main.py                 launcher (GUI, or --cli)
autotranslate/
  gui.py                Tkinter interface
  worker.py             the batch job, on a background thread
  backends.py           Ollama and OpenAI-compatible clients (streaming)
  imageutil.py          image discovery, natural sort, downscaling
  cli.py                headless mode
  config.py             settings and the default prompt
install.bat / start.bat / stop.bat      Windows
install.sh  / start.sh                  Linux
```

Only the standard library is required; Pillow is optional but recommended, as
it downscales pages before upload.
