"""Headless mode, for servers with no display.

    python main.py --cli --folder ./pages --model llava:13b
"""

import argparse
import queue
import sys

from . import config, imageutil
from .worker import TranslationJob


def main(argv):
    cfg = config.load()
    parser = argparse.ArgumentParser(prog="autotranslate", description=__doc__)
    parser.add_argument("--cli", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--folder", required=True, help="folder of images")
    parser.add_argument("--out", default="", help="output folder (default: alongside)")
    parser.add_argument("--backend", default=cfg["backend"], choices=["Ollama", "LM Studio"])
    parser.add_argument("--host", default="", help="override the server URL")
    parser.add_argument("--model", default=cfg["model"], help="model name")
    parser.add_argument("--language", default=cfg["language"])
    parser.add_argument("--think", action="store_true", help="enable thinking mode")
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--overwrite", action="store_true", help="redo existing .txt")
    parser.add_argument("--no-neighbours", action="store_true",
                        help="send only the current image")
    args = parser.parse_args(argv)

    cfg.update(
        {
            "backend": args.backend,
            "folder": args.folder,
            "output_folder": args.out,
            "model": args.model,
            "language": args.language,
            "thinking": args.think,
            "thinking_supported": True,
            "recursive": args.recursive,
            "skip_existing": not args.overwrite,
            "include_prev_image": not args.no_neighbours,
            "include_next_image": not args.no_neighbours,
        }
    )
    if args.host:
        key = "ollama_url" if args.backend == "Ollama" else "lmstudio_url"
        cfg[key] = args.host
    if not cfg["model"]:
        parser.error("--model is required (no model saved in config.json)")

    images = imageutil.find_images(args.folder, args.recursive)
    if not images:
        print("No images found in " + args.folder)
        return 1
    print("{} image(s) via {} / {}".format(len(images), args.backend, cfg["model"]))

    events = queue.Queue()
    job = TranslationJob(cfg, images, events)
    job.start()
    try:
        while True:
            kind, *payload = events.get()
            if kind == "log":
                print(payload[0])
            elif kind == "status":
                print("-> " + payload[0])
            elif kind == "finished":
                done, skipped, failed, summary = payload
                print("{} {} written, {} skipped, {} failed".format(
                    summary, done, skipped, failed))
                return 1 if failed else 0
    except KeyboardInterrupt:
        job.stop()
        print("\nStopping...")
        job.join(timeout=30)
        return 130


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
