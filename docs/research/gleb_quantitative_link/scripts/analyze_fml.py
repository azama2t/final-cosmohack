"""Inspect real FML image/box pairs and check if density is geometrically valid."""
from __future__ import annotations

import io
import json
import zipfile
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parents[1]
ZIP=ROOT/'data/raw/fml/FML_full_dataset.zip'
OUT=ROOT/'results/fml_samples'

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(ZIP) as z:
        names=set(z.namelist())
        labels=[n for n in names if n.startswith('fml/labels/yolo_format/test/') and n.endswith('.txt')]
        candidates=[]
        for name in labels:
            lines=[line for line in z.read(name).decode().splitlines() if line.strip()]
            image='fml/images/test/'+Path(name).stem+'.jpg'
            if image in names and lines:candidates.append((len(lines),name,image))
        candidates.sort()
        selected=[]
        for target in [1,2,3,5,8]:
            choice=min((x for x in candidates if x[0]>=target),key=lambda x:(x[0]-target,x[1]))
            if choice not in selected:selected.append(choice)
        result=[]
        for count,label_name,image_name in selected:
            image=Image.open(io.BytesIO(z.read(image_name))).convert('RGB')
            exif=image.getexif();gps=exif.get(34853)
            image_out=OUT/Path(image_name).name;image.save(image_out,quality=92)
            overlay=image.copy();draw=ImageDraw.Draw(overlay)
            lines=z.read(label_name).decode().splitlines()
            for line in lines:
                cls,cx,cy,w,h=map(float,line.split());W,H=image.size
                draw.rectangle(((cx-w/2)*W,(cy-h/2)*H,(cx+w/2)*W,(cy+h/2)*H),outline=(255,40,40),width=4)
            over_out=OUT/(Path(image_name).stem+'_boxes.jpg');overlay.save(over_out,quality=92)
            label_out=OUT/Path(label_name).name;label_out.write_bytes(z.read(label_name))
            result.append({'image':str(image_out.relative_to(ROOT)),'label':str(label_out.relative_to(ROOT)),
                           'overlay':str(over_out.relative_to(ROOT)),'boxes':count,'size_px':image.size,
                           'exif_count':len(exif),'gps_exif':bool(gps),'classes':sorted({line.split()[0] for line in lines})})
            print(image_name,count,image.size,'EXIF',len(exif),'GPS',bool(gps))
        summary={'zip_images':sum(n.lower().endswith('.jpg') for n in names),
                 'zip_labels':sum(n.endswith('.txt') and '/yolo_format/' in n for n in names),
                 'sampled':result}
        summary['coco_by_split']={split:{'images':len((d:=json.loads(z.read(f'fml/labels/coco_format/{split}.json')))['images']),
                                          'boxes':len(d['annotations'])} for split in ('train','val','test')}
        summary['coco_total_boxes']=sum(v['boxes'] for v in summary['coco_by_split'].values())
        (OUT/'summary.json').write_text(json.dumps(summary,indent=2))

if __name__=='__main__':main()
