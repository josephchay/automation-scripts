#!/usr/bin/env python3
import argparse
import bisect
import csv
import difflib
import json
import os
import re
import shutil
import sys
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass

import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi

try:
    import readline  # Enables arrow-key history in Linux/macOS terminals
except ImportError:
    pass


# --- Terminal ANSI Styling ---
class Style:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    MAGENTA = "\033[95m"
    RED = "\033[91m"

    @classmethod
    def disable(cls) -> None:
        for attr in ("RESET", "BOLD", "DIM", "CYAN", "GREEN", "YELLOW", "MAGENTA", "RED"):
            setattr(cls, attr, "")


if not sys.stdout.isatty():
    Style.disable()


class QuietYDLLogger:
    def debug(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        pass


@dataclass
class SearchMatch:
    index: int
    video_id: str
    video_title: str
    start_sec: float
    end_sec: float
    timestamp: str
    url: str
    matched_text: str
    context: str
    caption_type: str  # "CC", "Auto", or "SFX Video"
    language: str


def format_timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def extract_video_id(text: str) -> str | None:
    text = text.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", text):
        return text
    match = re.search(r"(?:v=|youtu\.be/|shorts/|embed/|live/)([A-Za-z0-9_-]{11})", text)
    return match.group(1) if match else None


def search_youtube_videos(query: str, max_results: int = 8) -> list[dict]:
    """Search YouTube via yt-dlp without an API key."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "logger": QuietYDLLogger(),
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
        entries = info.get("entries", []) if info else []
        videos = []
        for entry in entries:
            if entry and entry.get("id"):
                videos.append(
                    {
                        "id": entry["id"],
                        "title": entry.get("title") or f"Video ({entry['id']})",
                        "duration": entry.get("duration") or 0,
                        "channel": entry.get("channel") or entry.get("uploader") or "Unknown",
                    }
                )
        return videos


def fetch_transcript(
    video_id: str, languages: list[str]
) -> tuple[list[dict], str, str]:
    """
    Fetch transcript snippets with automatic fallback across versions of
    youtube-transcript-api. Returns (snippets, caption_type, language_code).
    """
    raw_items = None
    caption_type = "CC"
    lang_used = languages[0] if languages else "en"

    try:
        # Compatible with youtube-transcript-api >= 1.0.0
        api = YouTubeTranscriptApi()
        transcript_list = api.list(video_id)
        try:
            transcript = transcript_list.find_transcript(languages)
        except Exception:
            # Fallback to the first available transcript if requested lang is missing
            transcript = next(iter(transcript_list))
        caption_type = "Auto" if getattr(transcript, "is_generated", False) else "CC"
        lang_used = getattr(transcript, "language_code", lang_used)
        raw_items = transcript.fetch()
    except AttributeError:
        # Backward compatibility with youtube-transcript-api < 1.0.0
        try:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
            try:
                transcript = transcript_list.find_transcript(languages)
            except Exception:
                transcript = next(iter(transcript_list))
            caption_type = "Auto" if transcript.is_generated else "CC"
            lang_used = transcript.language_code
            raw_items = transcript.fetch()
        except Exception:
            return [], "", ""
    except Exception:
        return [], "", ""

    snippets = []
    for item in raw_items or []:
        text = item.get("text") if isinstance(item, dict) else getattr(item, "text", None)
        start = item.get("start") if isinstance(item, dict) else getattr(item, "start", None)
        dur = item.get("duration") if isinstance(item, dict) else getattr(item, "duration", 2.0)
        if text is not None and start is not None:
            cleaned = re.sub(r"\s+", " ", text).strip()
            if cleaned:
                snippets.append(
                    {
                        "text": cleaned,
                        "start": float(start),
                        "duration": float(dur or 2.0),
                    }
                )
    return snippets, caption_type, lang_used


def highlight_span(text: str, pattern: re.Pattern) -> str:
    return pattern.sub(
        lambda m: f"{Style.BOLD}{Style.YELLOW}{m.group(0)}{Style.RESET}", text
    )


def search_video_transcript(
    video: dict,
    keyword: str,
    mode: str,
    match_style: str,
    languages: list[str],
    fuzzy_threshold: float = 0.76,
) -> list[dict]:
    """Worker function to scan a single video's transcript."""
    snippets, caption_type, lang_used = fetch_transcript(video["id"], languages)
    if not snippets:
        return []

    raw_hits = []

    # Mode 2: Sound-effect cues inside captions: [Applause], (Music), *sigh*, ♪
    if mode == "sfx_tags":
        cue_regex = re.compile(r"(\[[^\]]+\]|\([^\)]+\)|\*[^\*]+\*|♪[^♪]+♪)")
        filter_pat = None
        if keyword and keyword != "*":
            pat_str = keyword if match_style == "regex" else re.escape(keyword)
            filter_pat = re.compile(pat_str, re.IGNORECASE)

        for i, snip in enumerate(snippets):
            cues = cue_regex.findall(snip["text"])
            if not cues:
                continue
            if filter_pat is None or any(filter_pat.search(c) for c in cues):
                ctx_slice = snippets[max(0, i - 1) : min(len(snippets), i + 2)]
                context = " ".join(s["text"] for s in ctx_slice)
                raw_hits.append(
                    {
                        "start": snip["start"],
                        "end": snip["start"] + snip["duration"],
                        "matched": ", ".join(cues),
                        "context": context,
                    }
                )
        return [
            {**hit, "video": video, "caption_type": caption_type, "language": lang_used}
            for hit in raw_hits
        ]

    # Mode 1: Dialog / Subtitle search (stitched across consecutive caption chunks)
    char_starts = []
    parts = []
    cursor = 0
    for snip in snippets:
        char_starts.append(cursor)
        parts.append(snip["text"])
        cursor += len(snip["text"]) + 1  # +1 for joining space
    full_text = " ".join(parts)

    if match_style == "fuzzy":
        query_words = keyword.lower().split()
        q_len = max(1, len(query_words))
        words_with_spans = [(m.group(0), m.start(), m.end()) for m in re.finditer(r"\S+", full_text)]
        idx = 0
        while idx <= len(words_with_spans) - q_len:
            window = words_with_spans[idx : idx + q_len]
            candidate = " ".join(w[0] for w in window).lower()
            score = difflib.SequenceMatcher(None, keyword.lower(), candidate).ratio()
            if score >= fuzzy_threshold:
                c_start, c_end = window[0][1], window[-1][2]
                s_idx = bisect.bisect_right(char_starts, c_start) - 1
                e_idx = bisect.bisect_right(char_starts, max(0, c_end - 1)) - 1
                ctx_slice = snippets[max(0, s_idx - 1) : min(len(snippets), e_idx + 2)]
                raw_hits.append(
                    {
                        "start": snippets[s_idx]["start"],
                        "end": snippets[e_idx]["start"] + snippets[e_idx]["duration"],
                        "matched": f"{full_text[c_start:c_end]} ({int(score * 100)}% match)",
                        "context": " ".join(s["text"] for s in ctx_slice),
                    }
                )
                idx += q_len  # Jump past matched span to avoid duplicate hits
            else:
                idx += 1
    else:
        try:
            pat_str = keyword if match_style == "regex" else re.escape(keyword)
            pattern = re.compile(pat_str, re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"Invalid regular expression: {exc}") from exc

        last_snip_idx = -1
        for m in pattern.finditer(full_text):
            c_start, c_end = m.start(), m.end()
            s_idx = bisect.bisect_right(char_starts, c_start) - 1
            e_idx = bisect.bisect_right(char_starts, max(0, c_end - 1)) - 1
            if s_idx == last_snip_idx:
                continue
            last_snip_idx = s_idx
            ctx_slice = snippets[max(0, s_idx - 1) : min(len(snippets), e_idx + 2)]
            raw_hits.append(
                {
                    "start": snippets[s_idx]["start"],
                    "end": snippets[e_idx]["start"] + snippets[e_idx]["duration"],
                    "matched": m.group(0),
                    "context": " ".join(s["text"] for s in ctx_slice),
                }
            )

    return [
        {**hit, "video": video, "caption_type": caption_type, "language": lang_used}
        for hit in raw_hits
    ]


def download_audio_clip(match: SearchMatch, pad_before: float = 2.0, pad_after: float = 5.0) -> None:
    """Download either a trimmed dialog/SFX clip (if ffmpeg exists) or full SFX audio."""
    os.makedirs("yt_clips", exist_ok=True)
    safe_title = re.sub(r"[^\w\-]+", "_", match.video_title)[:45].strip("_")
    has_ffmpeg = shutil.which("ffmpeg") is not None

    ydl_opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "logger": QuietYDLLogger(),
    }

    if match.caption_type == "SFX Video":
        out_tmpl = f"yt_clips/{safe_title}_{match.video_id}.%(ext)s"
        ydl_opts["outtmpl"] = out_tmpl
        if has_ffmpeg:
            ydl_opts["postprocessors"] = [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
            ]
        print(f"{Style.CYAN}Downloading full SFX audio...{Style.RESET}")
    else:
        start = max(0.0, match.start_sec - pad_before)
        end = max(start + 3.0, match.end_sec + pad_after)
        out_tmpl = f"yt_clips/{safe_title}_{int(start)}s-{int(end)}s.%(ext)s"
        ydl_opts["outtmpl"] = out_tmpl
        if has_ffmpeg:
            from yt_dlp.utils import download_range_func

            ydl_opts["download_ranges"] = download_range_func(None, [(start, end)])
            ydl_opts["force_keyframes_at_cuts"] = True
            ydl_opts["postprocessors"] = [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
            ]
            print(
                f"{Style.CYAN}Clipping audio [{format_timestamp(start)} -> {format_timestamp(end)}]...{Style.RESET}"
            )
        else:
            print(
                f"{Style.YELLOW}ffmpeg not found — downloading full audio track instead of trimmed clip...{Style.RESET}"
            )

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={match.video_id}"])
        print(f"{Style.GREEN}Saved to ./yt_clips/{Style.RESET}")
    except Exception as exc:
        print(f"{Style.RED}Download failed: {exc}{Style.RESET}")


def export_matches(matches: list[SearchMatch], fmt: str = "json") -> None:
    os.makedirs("yt_exports", exist_ok=True)
    filename = f"yt_exports/yt_search_results.{fmt}"
    rows = [asdict(m) for m in matches]
    if fmt == "csv":
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    else:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2, ensure_ascii=False)
    print(f"{Style.GREEN}Exported {len(matches)} result(s) to {filename}{Style.RESET}")


def execute_search(
    mode: str,
    target: str,
    keyword: str,
    max_videos: int = 8,
    languages: list[str] | None = None,
    match_style: str = "exact",
    workers: int = 6,
) -> list[SearchMatch]:
    languages = languages or ["en"]

    # Mode 3: Dedicated Sound Effect Videos
    if mode == "sfx_videos":
        query = target if "sound effect" in target.lower() or "sfx" in target.lower() else f"{target} sound effect"
        print(f"{Style.DIM}Searching YouTube for SFX clips: '{query}'...{Style.RESET}")
        videos = search_youtube_videos(query, max_results=max_videos)
        matches: list[SearchMatch] = []
        for idx, vid in enumerate(videos, start=1):
            dur = float(vid.get("duration") or 0)
            url = f"https://youtu.be/{vid['id']}"
            matches.append(
                SearchMatch(
                    index=idx,
                    video_id=vid["id"],
                    video_title=vid["title"],
                    start_sec=0.0,
                    end_sec=dur,
                    timestamp=format_timestamp(dur) if dur else "N/A",
                    url=url,
                    matched_text=vid["title"],
                    context=f"Channel: {vid['channel']} | Duration: {format_timestamp(dur) if dur else 'N/A'}",
                    caption_type="SFX Video",
                    language="-",
                )
            )
            print(
                f"\n{Style.BOLD}{Style.CYAN}[{idx}]{Style.RESET} {Style.BOLD}{vid['title']}{Style.RESET} "
                f"{Style.DIM}({format_timestamp(dur) if dur else 'N/A'} | {vid['channel']}){Style.RESET}"
            )
            print(f"    {Style.GREEN}{url}{Style.RESET}")
        return matches

    # Modes 1 & 2: Transcript Scanning (Single URL/ID or Multi-Video Search)
    direct_id = extract_video_id(target)
    if direct_id:
        videos = [{"id": direct_id, "title": f"YouTube Video ({direct_id})", "duration": 0}]
    else:
        print(f"{Style.DIM}Finding top {max_videos} videos for '{target}'...{Style.RESET}")
        videos = search_youtube_videos(target, max_results=max_videos)

    if not videos:
        print(f"{Style.RED}No videos found.{Style.RESET}")
        return []

    print(f"{Style.DIM}Scanning {len(videos)} transcript(s) in parallel...{Style.RESET}")
    all_raw_hits = []
    with ThreadPoolExecutor(max_workers=min(workers, len(videos))) as pool:
        futures = {
            pool.submit(
                search_video_transcript, vid, keyword, mode, match_style, languages
            ): vid
            for vid in videos
        }
        for future in as_completed(futures):
            try:
                hits = future.result()
                all_raw_hits.extend(hits)
            except ValueError as ve:
                print(f"{Style.RED}{ve}{Style.RESET}")
                return []
            except Exception:
                continue

    if not all_raw_hits:
        print(f"\n{Style.YELLOW}No matching lines or sound cues found.{Style.RESET}")
        return []

    matches = []
    current_vid = None
    hl_pattern = None
    if keyword and keyword != "*" and match_style != "fuzzy":
        pat_str = keyword if match_style == "regex" else re.escape(keyword)
        hl_pattern = re.compile(pat_str, re.IGNORECASE)

    for idx, hit in enumerate(all_raw_hits, start=1):
        vid = hit["video"]
        if vid["id"] != current_vid:
            current_vid = vid["id"]
            print(
                f"\n{Style.BOLD}{Style.MAGENTA}=== {vid['title']} ==={Style.RESET} "
                f"{Style.DIM}[{hit['caption_type']}:{hit['language']}]{Style.RESET}"
            )

        ts = format_timestamp(hit["start"])
        url = f"https://youtu.be/{vid['id']}?t={int(hit['start'])}"
        display_ctx = highlight_span(hit["context"], hl_pattern) if hl_pattern else hit["context"]

        matches.append(
            SearchMatch(
                index=idx,
                video_id=vid["id"],
                video_title=vid["title"],
                start_sec=hit["start"],
                end_sec=hit["end"],
                timestamp=ts,
                url=url,
                matched_text=hit["matched"],
                context=hit["context"],
                caption_type=hit["caption_type"],
                language=hit["language"],
            )
        )

        print(f"  {Style.BOLD}{Style.CYAN}[{idx}]{Style.RESET} {Style.YELLOW}[{ts}]{Style.RESET} \"{display_ctx}\"")
        print(f"      {Style.DIM}-> {url}{Style.RESET}")

    print(f"\n{Style.GREEN}{Style.BOLD}Found {len(matches)} match(es).{Style.RESET}")
    return matches


def post_search_actions(matches: list[SearchMatch]) -> None:
    """Interactive action bar for opening links, clipping audio, or exporting results."""
    if not matches:
        return
    while True:
        print(
            f"\n{Style.DIM}Actions: [o <num>] Open in browser | [d <num>] Download audio clip | "
            f"[e json|csv] Export | [Enter] New search{Style.RESET}"
        )
        cmd = input(f"{Style.BOLD}Action > {Style.RESET}").strip()
        if not cmd:
            break

        parts = cmd.split()
        action = parts[0].lower()

        if action in {"o", "open"} and len(parts) == 2 and parts[1].isdigit():
            idx = int(parts[1]) - 1
            if 0 <= idx < len(matches):
                print(f"Opening {matches[idx].url} ...")
                webbrowser.open(matches[idx].url)
            else:
                print("Invalid result number.")

        elif action in {"d", "download"} and len(parts) == 2 and parts[1].isdigit():
            idx = int(parts[1]) - 1
            if 0 <= idx < len(matches):
                download_audio_clip(matches[idx])
            else:
                print("Invalid result number.")

        elif action in {"e", "export"}:
            fmt = parts[1].lower() if len(parts) > 1 and parts[1].lower() in {"json", "csv"} else "json"
            export_matches(matches, fmt=fmt)

        else:
            print("Unknown command. Examples: 'o 1', 'd 2', 'e csv', or press Enter.")


def interactive_loop() -> None:
    print(f"{Style.BOLD}{Style.CYAN}====================================================={Style.RESET}")
    print(f"{Style.BOLD}{Style.CYAN}  YouTube Dialog, Subtitle & SFX Search CLI (v2){Style.RESET}")
    print(f"{Style.BOLD}{Style.CYAN}====================================================={Style.RESET}")

    while True:
        print("\nSelect mode:")
        print("  1. Search dialog / subtitles (supports multi-line phrases)")
        print("  2. Search sound-effect cues in captions (use '*' for ALL cues)")
        print("  3. Search dedicated sound-effect videos")
        print("  q. Quit")

        choice = input(f"\n{Style.BOLD}Choice (1/2/3/q): {Style.RESET}").strip().lower()
        if choice in {"q", "quit", "exit"}:
            break
        if choice not in {"1", "2", "3"}:
            print("Invalid choice.")
            continue

        if choice == "3":
            sfx_query = input("Sound effect prompt (e.g., vine boom, glass shatter): ").strip()
            if not sfx_query:
                continue
            limit_raw = input("Max results [default 8]: ").strip()
            limit = int(limit_raw) if limit_raw.isdigit() else 8
            matches = execute_search("sfx_videos", sfx_query, "", max_videos=limit)
            post_search_actions(matches)
            continue

        target = input("YouTube search topic OR specific Video URL/ID: ").strip()
        if not target:
            continue

        if choice == "2":
            keyword = input("SFX tag keyword (or '*' for all cues): ").strip() or "*"
            match_style = "exact"
        else:
            keyword = input("Dialog phrase / regex to find: ").strip()
            if not keyword:
                continue
            style_raw = input("Match type [1=Exact, 2=Fuzzy, 3=Regex | default 1]: ").strip()
            match_style = {"2": "fuzzy", "3": "regex"}.get(style_raw, "exact")

        lang_raw = input("Language code(s) [default 'en']: ").strip() or "en"
        languages = [c.strip() for c in lang_raw.split(",") if c.strip()]

        limit = 1
        if not extract_video_id(target):
            limit_raw = input("Videos to scan [default 8]: ").strip()
            limit = int(limit_raw) if limit_raw.isdigit() else 8

        mode_name = "sfx_tags" if choice == "2" else "dialog"
        matches = execute_search(
            mode=mode_name,
            target=target,
            keyword=keyword,
            max_videos=limit,
            languages=languages,
            match_style=match_style,
        )
        post_search_actions(matches)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Search YouTube videos for dialog, subtitles, or sound effects."
    )
    parser.add_argument("-t", "--target", help="YouTube search topic OR specific Video URL/ID")
    parser.add_argument("-q", "--query", help="Dialog phrase, regex, or SFX keyword (use '*' in sfx mode)")
    parser.add_argument(
        "-m",
        "--mode",
        choices=["dialog", "sfx-tags", "sfx-videos"],
        default="dialog",
        help="Search mode (default: dialog)",
    )
    parser.add_argument(
        "-s",
        "--style",
        choices=["exact", "fuzzy", "regex"],
        default="exact",
        help="Matching algorithm for dialog mode (default: exact)",
    )
    parser.add_argument("-n", "--max-videos", type=int, default=8, help="Max videos to scan (default: 8)")
    parser.add_argument("-l", "--lang", default="en", help="Comma-separated language codes (default: en)")
    parser.add_argument("--export", choices=["json", "csv"], help="Auto-export results to file")

    args = parser.parse_args()

    # If CLI arguments are passed, run directly; otherwise launch interactive terminal UI
    if args.target or (args.mode == "sfx-videos" and args.query):
        mode_map = {"dialog": "dialog", "sfx-tags": "sfx_tags", "sfx-videos": "sfx_videos"}
        target = args.target or args.query
        query = args.query or "*"
        langs = [c.strip() for c in args.lang.split(",") if c.strip()]

        matches = execute_search(
            mode=mode_map[args.mode],
            target=target,
            keyword=query,
            max_videos=args.max_videos,
            languages=langs,
            match_style=args.style,
        )
        if args.export and matches:
            export_matches(matches, fmt=args.export)
        elif sys.stdin.isatty() and matches:
            post_search_actions(matches)
    else:
        interactive_loop()


if __name__ == "__main__":
    main()
    