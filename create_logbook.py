


# Parse all subfolders and print the name of *.tar files
import os
import tarfile

def parse_logs():

    folders = [
    	'/home/mavlab/paparazzi/var/logs',
    	'/home/mavlab/tudelft_net/staff-umbrella/Navy/flight_logs',
    ]


    for f in folders:
        print("Folder", f)
        for root, dirs, files in os.walk(f):
            none = True
            for file in files:
                if file.endswith('.data'):
                    data = root+'/'+file
                    #if '2024_11_' in data:
                    #print('DATA',data)
                    # os.system(f"cat {data} | grep '\\[PFC\\]' | grep -v ': true' | grep -v ': false'")
                    os.system(f"./cpp/build/paparazzi_log_parsing \"{data}\"")
                    none = False
                if file.endswith('.insv'):
                    #print('VID',root, file)
                    none = False

parse_logs()

# cat /home/mavlab/tudelft_net/staff-umbrella/Navy/flight_logs/2024_09_20_troia_25kg_hydrogen/24_09_20__09_59_09.data
# | grep '\[PFC\]' | grep -v ': true' | grep -v ': false'


