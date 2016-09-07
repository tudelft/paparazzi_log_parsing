import numpy as np

from mpl_toolkits.mplot3d import Axes3D
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

from io import StringIO






class rotorcraft_fp_class:
    """Rotorcraft Class"""
    t = 0

    def f(self):
        return 'hello world'




def plot_log_file(filename, nr):

    gps_int = []
    rotorcraft_fp = []
    rotorcraft_status_txt = []
    motor = []
    imu_mag_raw_txt = []
    imu_gyro_scaled_txt = []


    tags = {}
    ids = {}
    autopilot_version = {}

    with open(filename) as f:
        for line in f:
            
            d = line.strip().split(" ")
            if len(d) > 2:
                tags[d[2]] = 1;
                ids[d[1]] = 1;
            line = line.replace("replay", "1000")
            #if "replay" in line:
            #    return "REPLAY";
            #elif "NPS_" in line:
            #    return "SIM";
            #el
            if "GPS_INT" in line:
                gps_int.append(line.replace("GPS_INT ", ""))
            elif "MOTOR" in line:
                motor.append(line.replace("MOTOR ", ""))
            elif "IMU_MAG_RAW" in line:
                imu_mag_raw_txt.append(line.replace("IMU_MAG_RAW ", ""))
            elif "IMU_GYRO_SCALED" in line:
                imu_gyro_scaled_txt.append(line.replace("IMU_GYRO_SCALED ", ""))
            elif "ROTORCRAFT_STATUS" in line:
                rotorcraft_status_txt.append("".join(line.split("ROTORCRAFT_STATUS ")))
            elif "ROTORCRAFT_FP" in line:
                rotorcraft_fp.append("".join(line.split("ROTORCRAFT_FP ")))
            elif "AUTOPILOT_VERSION" in line:
                autopilot_version[line.strip().split("AUTOPILOT_VERSION")[1]] = 1


    #print(" - ", ", ".join(sorted(ids.keys())))
    #print(" - ", ", ".join(sorted(tags.keys())))
    #print(" - ", ", ".join(autopilot_version.keys()))

    #g = np.fromstring(s, dtype=None, sep=' ')
    #g = np.genfromtxt(StringIO(s), delimiter=' ', dtype=None)

    g = np.empty([0,0]);
    if len(gps_int) > 0:
        g = np.loadtxt(StringIO("".join(gps_int)), delimiter=' ', dtype=None)
        
    r = np.empty([0,0])
    if len(rotorcraft_fp) > 0:
        r = np.loadtxt(StringIO("".join(rotorcraft_fp)))
             
    rotorcraft_status = np.empty([0,0])
    if len(rotorcraft_status_txt) > 0:
        rotorcraft_status = np.loadtxt(StringIO("".join(rotorcraft_status_txt)))
             
    m = np.empty([0,0])
    if len(motor) > 0:
        m = np.loadtxt(StringIO("".join(motor)))
             
    imu_mag_raw = np.empty([0,0])
    if len(imu_mag_raw_txt) > 0:
        imu_mag_raw = np.loadtxt(StringIO("".join(imu_mag_raw_txt)))
             
    imu_gyro_scaled = np.empty([0,0])
    if len(imu_gyro_scaled_txt) > 0:
        imu_gyro_scaled = np.loadtxt(StringIO("".join(imu_gyro_scaled_txt)))


    #print(g)
    #print(r)

    ###########################
    # Guess File Type:

    fig = plt.figure(nr)

    #####
    # RAW
    if imu_mag_raw.size > 0:
        ax = fig.gca( projection='3d')
        ax.plot(imu_mag_raw[:,2], imu_mag_raw[:,3], imu_mag_raw[:,4],label='imu_mag_raw')
        ax.legend()
        #ax.grid()
        
    #############
    # ONBOARD LOG
    if imu_gyro_scaled.size > 0:
        TURN_RATE_FRAC = 2^12
        plt.plot(imu_gyro_scaled[:,0], imu_gyro_scaled[:,2]/TURN_RATE_FRAC)
        plt.plot(imu_gyro_scaled[:,0], imu_gyro_scaled[:,2]/TURN_RATE_FRAC)
        plt.plot(imu_gyro_scaled[:,0], imu_gyro_scaled[:,3]/TURN_RATE_FRAC)
        plt.grid()
        plt.title(filename)
        return "RAW, " + ", ".join(sorted(ids.keys()))


    ############
    # ROTORCRAFT
    elif g.size > 8:
        inflight = 0;
        if rotorcraft_status.size > 28:
            np.sum(rotorcraft_status[:,8])
        title = "ROTOR, " + ", ".join(sorted(ids.keys())) + ", " + str(inflight)
        if "NPS_SENSORS_SCALED" in tags:
            title = "SIMULATION, " + ", ".join(sorted(ids.keys()))  + ", " + str(inflight)

        plt.subplot(2, 2, 1)
        plt.plot(g[:,0], g[:,8]/1000.0)
        plt.title(filename)
        plt.ylabel('alt [m]')
        plt.grid()
        plt.subplot(2, 2, 2)
        if m.size > 28:
            plt.plot(m[:,0], m[:,2])
        plt.ylabel('rpm [1/m]')
        plt.xlabel('time [s]')
        plt.grid()
        plt.subplot(2, 2, 3)
        plt.plot(r[:,0], r[:,16])
        plt.grid()
        plt.title(title)
        plt.subplot(2, 2, 4)
#        print(rotorcraft_status.size > )
        if rotorcraft_status.size > 28:
           plt.plot(rotorcraft_status[:,0], rotorcraft_status[:,12]/10.0)
        plt.grid()
        plt.ylabel('vbat [V]')

        return title
 

    return "NODATA"

if __name__ == "__main__":
    print('Start')
    #filename = '16_08_02_Heemskerk/16_08_02__15_13_52.data'
    #filename = '16_04_11_monday/16_04_11__18_24_17.data'
    #filename = '16_08_01 Kalmthout auto2\sd_kaart/16_08_01__22_33_34_SD_no_GPS.data'
    filename = '15_08_05__10_37_24.data'

    p = plot_log_file(filename,1)

    print('Ready')

    p.show()

    #pp = PdfPages('test.pdf')
    #pp.savefig(1)
    #pp.close()
    

    

