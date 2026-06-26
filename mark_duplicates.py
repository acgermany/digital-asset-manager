import pandas as pd 
import re

df = pd.DataFrame(r'inventory_export.csv')

bad_folders = ["Unorganized", "#recycle"]


x = [".mov", ".mp4", ".wav", ".mp3", "(1)"]   # your list of partial strings

pattern = "|".join(map(re.escape, x))   # safe regex pattern


x = ["Unorganized", '#recycle']   # your list of partial strings

pattern2 = "|".join(map(re.escape, x))   # safe regex pattern



dfilter = ((df['isDuplicate'] == 1) 
    & (df['filename'].str.contains(pattern, case=False, na=False))
    #\& (df['filepath'].str.contains(pattern2, case=False, na=False))
    & (df['toDelete'] != 1)
)


df.loc[dfilter][['filename', 'filesize', 'filepath', 'toDelete']].sort_values(by='filesize', ascending = False)

df.loc[dfilter, 'toDelete'] = 1

#Delete queue
df[df['toDelete'] == 1]


print(result)