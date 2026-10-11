"""Extract the six supplied poses per sheet, key green, and register silhouettes."""
import json
from pathlib import Path
from PIL import Image, ImageDraw

ASSETS = Path(__file__).resolve().parent / "assets"


def remove_green(image):
    output = image.convert("RGBA")
    result = []
    for r, g, b, a in output.getdata():
        excess = g - max(r, b)
        if g > 100 and excess > 35:
            if r < 45 and b < 45:
                result.append((0, 0, 0, 0))
                continue
            alpha = max(0, min(255, round(255 - excess)))
            # Suppress green spill only on the keyed silhouette boundary.
            g = min(g, max(r, b))
            result.append((r, g, b, min(a, alpha)))
        else:
            result.append((r, g, b, a))
    output.putdata(result)
    return output


def spans(projection):
    runs, start = [], None
    for i, on in enumerate(projection + [0]):
        if on and start is None:
            start = i
        elif not on and start is not None:
            if i - start > 70:
                runs.append((start, i))
            start = None
    return runs


def main():
    directory = ASSETS / "frames"
    directory.mkdir(exist_ok=True)
    contact = Image.new("RGB", (6 * 256, 2 * 280), "#eeeeee")
    report = {}
    for row, state in enumerate(("normal", "angry")):
        source = remove_green(Image.open(ASSETS / f"{state}_source.png"))
        frames = []
        for band in range(2):
            # Use actual empty gutters: tails cross the nominal 512px grid.
            strip = source.crop((0, band * source.height // 2, source.width, (band + 1) * source.height // 2))
            mask = strip.getchannel("A").point(lambda a: 255 if a > 150 else 0)
            runs = spans(list(mask.getprojection()[0]))
            if len(runs) != 3:
                raise ValueError(f"Expected three figures in {state} band {band}, got {runs}")
            for left, right in runs:
                tile = strip.crop((max(0,left-3),0,min(strip.width,right+3),strip.height))
                bounds = tile.getchannel("A").getbbox()
                tile = tile.crop(bounds)
                height = 440
                width = round(tile.width * height / tile.height)
                if width > 480:
                    raise ValueError("Pose exceeds the shared canvas")
                tile = tile.resize((width,height), Image.Resampling.LANCZOS)
                frame = Image.new("RGBA",(512,512))
                frame.alpha_composite(tile,((512-width)//2,492-height))
                i = len(frames)
                frame.save(directory / f"{state}_{i:02}.png")
                frames.append(frame)
                contact.paste(frame.resize((256,256),Image.Resampling.LANCZOS),(i*256,row*280),frame.resize((256,256),Image.Resampling.LANCZOS))
                ImageDraw.Draw(contact).text((i*256+10,row*280+258),f"{state} {i+1}",fill="#222222")
        durations = [180]*5 + [3180 if state=="normal" else 780]
        previews = []
        for frame in frames:
            flat = Image.new("RGB",frame.size,"#eeeeee")
            flat.paste(frame,mask=frame.getchannel("A"))
            previews.append(flat)
        previews[0].save(ASSETS/f"{state}_preview.gif",save_all=True,append_images=previews[1:],duration=durations,loop=0)
        report[state] = {"frames":6,"canvas":[512,512],"height":440,"baseline":492,"durations_ms":durations}
    contact.save(ASSETS / "animation_contact.png")
    (directory / "manifest.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
