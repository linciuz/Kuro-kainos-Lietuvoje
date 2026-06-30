from PIL import Image, ImageDraw

def lerp(a, b, t): return tuple(int(a[i]+(b[i]-a[i])*t) for i in range(3))

def make(size, maskable=False):
    S = 1024  # draw big, downscale for crispness
    img = Image.new("RGB", (S, S), (0,86,179))
    px = img.load()
    top, bot = (0,122,255), (0,70,160)   # #007AFF -> darker blue
    for y in range(S):
        c = lerp(top, bot, y/S)
        for x in range(S):
            px[x,y] = c
    d = ImageDraw.Draw(img)
    W = (255,255,255)
    scr = (0,78,170)

    # If maskable, keep art inside ~80% safe zone (more padding)
    pad = 0.18 if maskable else 0.0
    def sx(v): return int((pad + v*(1-2*pad))*S)
    # scale helper for coordinates given as 0..1
    def R(x0,y0,x1,y1,r,fill):
        d.rounded_rectangle([sx(x0),sx(y0),sx(x1),sx(y1)], radius=int(r*(1-2*pad)*S), fill=fill)

    # pump body
    R(0.17,0.16,0.55,0.80, 0.05, W)
    # display screen
    R(0.21,0.21,0.50,0.36, 0.02, scr)
    # slot line under screen
    R(0.21,0.42,0.50,0.47, 0.012, scr)
    # base plinth
    R(0.15,0.80,0.57,0.86, 0.012, W)
    # nozzle riser (vertical bar right of body) + top connector
    R(0.50,0.16,0.62,0.18, 0.0, W)         # top connector
    R(0.58,0.16,0.66,0.55, 0.03, W)        # riser
    # nozzle head pointing down
    R(0.60,0.50,0.74,0.57, 0.02, W)
    R(0.69,0.50,0.74,0.66, 0.02, W)

    img = img.resize((size,size), Image.LANCZOS)
    return img

make(512).save("icon-512.png")
make(192).save("icon-192.png")
make(512, maskable=True).save("icon-512-maskable.png")
make(180).save("apple-touch-icon.png")
print("icons written")
