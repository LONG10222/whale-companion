"""CUDA lives only in this child process; exiting releases its CUDA context."""
import contextlib
import json
import os
import sys
import time
import traceback


def reply(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def main():
    # Windows defaults can differ from the parent's UTF-8 pipe encoding.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    config = json.loads(sys.argv[1])
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    started = time.monotonic()
    try:
        with contextlib.redirect_stdout(sys.stderr):
            import numpy as np
            import soundfile as sf
            import torch
            from qwen_tts import Qwen3TTSModel

            torch.set_num_threads(config["cpu_threads"])
            if not torch.cuda.is_available():
                raise RuntimeError("CUDA unavailable: install a Blackwell-compatible PyTorch build")
            torch.cuda.set_per_process_memory_fraction(config["gpu_memory_fraction"], 0)
            model = Qwen3TTSModel.from_pretrained(
                config["model_dir"], device_map="cuda:0", dtype=torch.bfloat16,
                attn_implementation="sdpa", local_files_only=True,
            )
        reply({"ready": True, "load_seconds": round(time.monotonic() - started, 3),
               "speakers": model.get_supported_speakers()})
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        reply({"ready": False, "error": str(exc)})
        return
    for line in sys.stdin:
        try:
            request = json.loads(line)
            started = time.monotonic()
            torch.cuda.reset_peak_memory_stats()
            with contextlib.redirect_stdout(sys.stderr), torch.inference_mode():
                wavs, sr = model.generate_custom_voice(
                    text=request["text"], language="Chinese", speaker=request["voice"],
                    do_sample=False, subtalker_dosample=False, max_new_tokens=660,
                )
                audio = np.asarray(wavs[0], dtype=np.float32)
                duration = len(audio) / sr
                if duration <= 0 or duration > config["max_audio_seconds"]:
                    raise ValueError(f"Audio duration {duration:.2f}s is outside the permitted range; shorten the text")
                if not np.isfinite(audio).all() or np.sqrt(np.mean(audio * audio)) < 0.0001:
                    raise ValueError("Generated audio is invalid or silent")
                sf.write(request["path"], audio, sr, format="WAV", subtype="PCM_16")
                info = {"ok": True, "seconds": round(duration, 3), "sample_rate": sr,
                        "generation_seconds": round(time.monotonic() - started, 3),
                        "peak_allocated_mib": round(torch.cuda.max_memory_allocated() / 1024**2, 1),
                        "peak_reserved_mib": round(torch.cuda.max_memory_reserved() / 1024**2, 1)}
                del wavs, audio
                torch.cuda.empty_cache()
            reply(info)
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            reply({"ok": False, "error": str(exc)})


if __name__ == "__main__":
    main()
