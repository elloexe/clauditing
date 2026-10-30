"""
Publish a batch of finished clips so they show up in the app under "Made by Claude".

  python tools/publish_batch.py batch.json

batch.json:
{
  "tag": "clips-2026-11-02",           // must start with "clips-"
  "title": "Week of Nov 2",
  "notes": "Best moments from Saturday's session",
  "clips": [
    {"file": "out/monday.mp4", "title": "Boss fight comeback",
     "caption": "this was NOT planned", "hashtags": "#roblox #fyp", "post_on": "2026-11-02"}
  ]
}

Makes a thumbnail for each clip, writes manifest.json and uploads everything as a GitHub Release
(needs the `gh` CLI, logged in).
"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import clauditing  # noqa: E402


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    plan_path = sys.argv[1]
    with open(plan_path, encoding="utf-8") as f:
        plan = json.load(f)
    tag = plan["tag"]
    if not tag.startswith("clips-"):
        sys.exit("tag must start with 'clips-'")
    base = os.path.dirname(os.path.abspath(plan_path))
    tmp = tempfile.mkdtemp()
    files, entries = [], []
    for c in plan["clips"]:
        src = c["file"] if os.path.isabs(c["file"]) else os.path.join(base, c["file"])
        name = os.path.basename(src)
        thumb = os.path.splitext(name)[0] + ".jpg"
        thumb_path = os.path.join(tmp, thumb)
        clauditing.run(["ffmpeg", "-v", "error", "-y", "-ss", str(c.get("thumb_at", 1)), "-i", src,
                        "-frames:v", "1", "-vf", "scale=360:-2", thumb_path])
        files += [src, thumb_path]
        entries.append({"file": name, "thumb": thumb, "title": c.get("title", ""),
                        "caption": c.get("caption", ""), "hashtags": c.get("hashtags", ""),
                        "post_on": c.get("post_on", "")})
    manifest = os.path.join(tmp, "manifest.json")
    with open(manifest, "w", encoding="utf-8") as f:
        json.dump({"title": plan.get("title", tag), "notes": plan.get("notes", ""), "clips": entries}, f, indent=2)
    files.append(manifest)
    subprocess.run(["gh", "release", "create", tag, *files, "--title", plan.get("title", tag),
                    "--notes", plan.get("notes", ""), "--latest=false"], check=True)
    print(f"published {len(entries)} clips as {tag}")


if __name__ == "__main__":
    main()
