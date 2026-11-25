import sys
from pathlib import Path
from PIL import Image, ImageGrab


def main():
    image = ImageGrab.grabclipboard()
    if isinstance(image,Image.Image):
        image.save(sys.stdout.buffer, 'PNG')
    elif isinstance(image,list) and len(image)==1 and Path(image[0]).suffix.lower() in {'.jpg','.jpeg','.webp','.png'}:
        with Path(image[0]).open('rb') as f: sys.stdout.buffer.write(f.read(12*1024*1024+1))
    else:
        sys.exit('剪贴板中没有单张图片，请先复制一张截图。')


if __name__=='__main__': main()
