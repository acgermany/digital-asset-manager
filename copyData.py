import pandas as pd
import os
import shutil

df = pd.read_csv('/mnt/nas/20250206_inventory_to_migrate.csv').sort_values(by='filesize', ascending = True)

for index, row in df.iterrows():
    source = os.path.join('/mnt/nas', row['filepath'].lstrip('/'))
    destination = '/mnt/nas/DEDUPED_MEDIA'
    print('------')
    print(source)
    print(destination)
    input()
    shutil.copy(source, destination)