# YouTube Video Searcher

## Pacakages

In terminal:
```
pip install yt-dlp youtube-transcript-api
```

## One-Line CLI Commands (Scriptable)

Find an exact dialog quote across 10 videos (even if split across subtitle lines):
```Bash
python yt_search_cli.py -t "interstellar docking scene" -q "no it's necessary" -n 10
```

Fuzzy search (handles typos or slight wording differences):
```Bash
python yt_search_cli.py -t "dark knight interrogation" -q "you have nothing to threaten me" -s fuzzy
```

Extract ALL sound-effect cues ([Applause], [Explosion], etc.) from a specific video:
```Bash
python yt_search_cli.py -t "https://youtu.be/UF8uR6Z6KLc" -m sfx-tags -q "*"
```

Search dedicated sound-effect clips and export results to CSV:
```Bash
python yt_search_cli.py -m sfx-videos -q "metal pipe falling" -n 5 --export csv
```

Interactive Terminal Mode
Run python `yt_search_cli.py` with no flags to use the interactive prompt, then use the post-search action bar (`o 1` to open timestamp in browser, `d 1` to download a trimmed `.mp3` audio clip into `./yt_clips/`, or `e json` to export).
