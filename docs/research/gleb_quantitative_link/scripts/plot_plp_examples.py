"""Visualize real PLP2019 Sentinel-2 pixels carrying known target fractions."""
import csv
from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'data/raw/plp2019/extracted/PLP2019_dataset/S2_satellite_images_nc'
OUT=ROOT/'results/plp2019'

def band(ds,wavelength):
    key=min((k for k in ds.data_vars if k.startswith('rhos_')),key=lambda k:abs(int(k.split('_')[-1])-wavelength))
    return ds[key].values

def main():
    rows=list(csv.DictReader((OUT/'labeled_pixels.csv').open(newline='')))
    dates=sorted({r['date'] for r in rows});fig,axes=plt.subplots(2,3,figsize=(14,8));axes=axes.flat
    for i,date in enumerate(dates):
        r=max((r for r in rows if r['date']==date),key=lambda x:int(x['plastic_percent']))
        nc=next(BASE.glob(f'*_{date}_*.nc'))
        with xr.open_dataset(nc,engine='h5netcdf') as ds:
            rgb=np.stack([band(ds,665),band(ds,560),band(ds,492)],axis=-1)
        lo=np.nanpercentile(rgb,2,axis=(0,1));hi=np.nanpercentile(rgb,98,axis=(0,1))
        rgb=np.clip((rgb-lo)/(hi-lo),0,1);ax=axes[i];ax.imshow(rgb,interpolation='nearest')
        col,row=int(r['pixel_col']),int(r['pixel_row'])
        ax.add_patch(plt.Rectangle((col-.5,row-.5),1,1,fill=False,edgecolor='yellow',linewidth=2))
        ax.set(xlim=(max(0,col-9),min(rgb.shape[1],col+10)),ylim=(min(rgb.shape[0],row+8),max(0,row-9)))
        ax.set_title(f"{date} | {r['pixel_name']} | plastic {r['plastic_percent']}%\nNIR={float(r['rhos_nir']):.3f}, FDI={float(r['fdi']):.3f}",fontsize=10)
    axes[-1].axis('off');fig.suptitle('PLP2019: artificial target coverage measured in 10 m Sentinel-2 pixels',fontsize=14)
    fig.tight_layout();fig.savefig(OUT/'five_real_labeled_pixels.png',dpi=180);plt.close(fig)

if __name__=='__main__':main()
