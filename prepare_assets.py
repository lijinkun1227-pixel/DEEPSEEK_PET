"""Extract the supplied character with a silhouette mask; preserve white clothing."""
from pathlib import Path
from collections import deque
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent / "assets"
# Source coordinates follow the original artwork, not its white/dark colors.
BODY = [
    (633,356),(653,353),(676,355),(697,362),(720,359),(743,369),
    (759,381),(785,393),(802,414),(819,433),(826,457),(830,480),
    (844,506),(841,534),(836,554),(840,576),(847,604),(861,630),
    (878,652),(879,660),(868,668),(845,676),(826,674),(823,696),
    (837,720),(853,748),(859,763),(874,750),(881,730),(874,712),
    (859,697),(854,684),(864,685),(886,694),(904,714),(920,708),
    (939,693),(948,681),(950,704),(943,726),(928,745),(910,752),
    (901,768),(891,783),(882,791),(893,808),(900,833),(899,856),
    (896,876),(911,871),(923,859),(925,841),(934,852),(940,875),
    (937,895),(925,912),(904,925),(883,935),(858,935),(838,932),
    (822,940),(798,944),(774,943),(752,948),(727,942),(703,946),
    (675,948),(642,944),(617,944),(593,936),(566,938),(540,937),
    (516,932),(496,936),(471,931),(452,926),(430,932),(407,929),
    (391,923),(385,910),(395,892),(402,879),(390,868),(376,849),
    (371,829),(372,804),(378,779),(390,751),(407,727),(418,704),
    (430,681),(443,667),(430,675),(416,672),(409,661),(412,647),
    (433,631),(443,611),(444,598),(420,597),(401,590),(387,579),
    (402,574),(420,557),(433,539),(449,521),(451,512),(437,515),
    (428,506),(424,491),(440,496),(460,487),(482,470),(490,450),
    (494,429),(505,412),(521,405),(529,393),(546,385),(565,370),
    (584,367),(598,359),(618,358)
]
CURL = [
    (632,347),(627,339),(630,326),(641,316),(657,308),(680,307),
    (702,311),(721,323),(737,341),(746,362),(744,379),(735,392),
    (723,382),(730,370),(730,355),(720,341),(704,330),(686,326),
    (666,325),(651,329),(640,337),(639,345)
]

def main() -> None:
    source = Image.open(ROOT / "reference_full.png").convert("RGBA")
    mask = Image.new("L", source.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.polygon(BODY, fill=255)
    draw.polygon(CURL, fill=255)
    # Remove white connected to the exterior, keeping enclosed clothing opaque.
    alpha = mask.load()
    pixels = source.load()
    queue = deque()
    for y in range(source.height):
        for x in range(source.width):
            if alpha[x,y] and min(pixels[x,y][:3])>237:
                if any(not (0<=nx<source.width and 0<=ny<source.height) or not alpha[nx,ny]
                       for nx,ny in ((x-1,y),(x+1,y),(x,y-1),(x,y+1))):
                    alpha[x,y]=0
                    queue.append((x,y))
    while queue:
        x,y=queue.popleft()
        for nx,ny in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
            if 0<=nx<source.width and 0<=ny<source.height and alpha[nx,ny] and min(pixels[nx,ny][:3])>237:
                alpha[nx,ny]=0
                queue.append((nx,ny))
    # The detached heart in the supplied comic is not part of the character.
    draw.polygon([(388,712),(416,694),(437,700),(430,741),(405,754),(383,748)],fill=0)
    source.putalpha(mask.filter(ImageFilter.GaussianBlur(0.45)))
    source = source.crop((355,295,958,955))
    source.thumbnail((468,492), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (512,512))
    canvas.alpha_composite(source, ((512-source.width)//2,512-source.height-10))
    canvas.save(ROOT / "deepseek_chibi.png")
    print("Prepared 512x512 transparent character.")

if __name__ == "__main__":
    main()
