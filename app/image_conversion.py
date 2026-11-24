"""Lossless conversion where possible, with collision-safe file publication."""
from pathlib import Path
import os
import io
import shutil
import tempfile

IMAGE_FORMATS = ('original', 'jpg', 'png', 'webp', 'tiff', 'bmp')


def publish_image(source, directory, extension=None):
    """Copy a completed image to a unique final name, never overwrite a file."""
    source, directory = Path(source), Path(directory)
    suffix = '.' + extension if extension else source.suffix
    index = 0
    while True:
        destination = directory / (source.stem + (f'_{index}' if index else '') + suffix)
        try:
            out = destination.open('xb')
        except FileExistsError:
            index += 1
            continue
        try:
            with out, source.open('rb') as inp:
                shutil.copyfileobj(inp, out)
        except BaseException:
            destination.unlink()
            raise
        return destination


def convert_image(source, target):
    from PIL import Image, ImageOps
    source = Path(source)
    if target not in IMAGE_FORMATS:
        raise ValueError('Invalid image format')
    if target == 'original':
        return source
    with Image.open(source) as opened:
        if getattr(opened, 'n_frames', 1) > 1:
            raise ValueError('Animated/multi-frame image: select Original to preserve all frames')
        image = ImageOps.exif_transpose(opened)
        fmt = {'jpg':'JPEG', 'png':'PNG', 'webp':'WEBP', 'tiff':'TIFF', 'bmp':'BMP'}[target]
        if opened.format == fmt:
            return source
        options = {}
        profile = image.info.get("icc_profile")
        if image.mode == "CMYK" and target != "tiff":
            # Convert to sRGB using the embedded CMYK profile when possible.
            if profile:
                try:
                    from PIL import ImageCms
                    srgb = ImageCms.createProfile("sRGB")
                    image = ImageCms.profileToProfile(image, ImageCms.ImageCmsProfile(io.BytesIO(profile)), srgb, outputMode="RGB")
                    profile = ImageCms.ImageCmsProfile(srgb).tobytes()
                except (OSError, ValueError, ImageCms.PyCMSError):
                    image = image.convert("RGB")
                    profile = None
            else:
                image = image.convert("RGB")
        if target == "png" and image.mode in ("RGBa", "La"):
            image = image.convert("RGBA" if image.mode == "RGBa" else "LA")
        if target == "png" and "transparency" in image.info and image.mode in ("P", "RGB", "L", "1"):
            # Materialize palette or color-key transparency as real alpha.
            image = image.convert("RGBA" if image.mode in ("P", "RGB") else "LA")
            image.info.pop("transparency", None)
        if target == "png" and image.mode not in ("1", "L", "LA", "I", "I;16", "I;16B", "RGB", "RGBA", "P"):
            image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            profile = None
        if target in ('jpg','bmp'):
            if 'A' in image.getbands() or 'transparency' in image.info:
                rgba = image.convert('RGBA')
                background = Image.new('RGB', rgba.size, 'white')
                background.paste(rgba, mask=rgba.getchannel('A'))
                image = background
            else:
                image = image.convert('RGB')
        elif target == 'webp':
            image = image.convert('RGBA' if 'A' in image.getbands() or 'transparency' in image.info else 'RGB')
        elif image.mode == 'P':
            image = image.convert('RGBA' if 'transparency' in image.info else 'RGB')
        if target == 'jpg': options.update(quality=100, subsampling=0)
        if target == 'webp': options.update(lossless=True, quality=100, method=6, exact=True)
        if target == 'tiff': options['compression'] = 'tiff_lzw'
        if target == 'png': options['compress_level'] = 9
        if profile and target != 'bmp' and (opened.mode not in ('L','LA','1') or image.mode in ('L','LA','1')): options['icc_profile'] = profile
        if target in ('jpg','png','webp','tiff') and image.getexif(): options['exif'] = image.getexif().tobytes()
        fd, temporary = tempfile.mkstemp(dir=source.parent, suffix='.tmp')
        os.close(fd)
        try:
            image.save(temporary, format=fmt, **options)
            index = 0
            while True:
                destination = source.with_name(source.stem + (f'_{index}' if index else '') + '.' + target)
                try:
                    with destination.open('xb') as out, open(temporary,'rb') as inp:
                        try: shutil.copyfileobj(inp, out)
                        except BaseException:
                            out.close(); destination.unlink(); raise
                    return destination
                except FileExistsError:
                    index += 1
        finally:
            os.unlink(temporary)
