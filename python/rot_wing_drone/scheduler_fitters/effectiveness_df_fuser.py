import numpy as np

def fuse_doublet_dfs(df_list):
    doublet_actuator = []
    airspeed = []
    wing_angle = []
    cmd_af = []
    roll_eff = []
    pitch_eff = []
    yaw_eff = []
    if len(df_list) > 1:
        for i in range(len(df_list)):
            doublet_actuator = np.append(doublet_actuator, df_list[i]['idx'].tolist())
            airspeed = np.append(airspeed, df_list[i]['airspeed'].tolist())
            wing_angle = np.append(wing_angle, df_list[i]['wing_angle'].tolist())
            try:
                cmd_af = np.append(cmd_af, df_list[i]['cmd_af'].tolist())
            except:
                for j in range(len(df_list[i]['idx'].tolist())):
                    cmd_af = np.append(cmd_af, '[5000.0 5000.0 5000.0 5000.0 0.0 0.0 0.0 0.0 0.0]')
            roll_eff = np.append(roll_eff, df_list[i]['roll_eff'].tolist())
            pitch_eff = np.append(pitch_eff, df_list[i]['pitch_eff'].tolist())
            yaw_eff = np.append(yaw_eff, df_list[i]['yaw_eff'].tolist())
    else:
        doublet_actuator = np.append(doublet_actuator, df_list[0]['idx'].tolist())
        airspeed = np.append(airspeed, df_list[0]['airspeed'].tolist())
        wing_angle = np.append(wing_angle, df_list[0]['wing_angle'].tolist())
        try:
            cmd_af = np.append(cmd_af, df_list[0]['cmd_af'].tolist())
        except:
            for j in range(len(df_list[0]['idx'].tolist())):
                cmd_af = np.append(cmd_af, '[5000.0 5000.0 5000.0 5000.0 0.0 0.0 0.0 0.0 0.0]')
        roll_eff = np.append(roll_eff, df_list[0]['roll_eff'].tolist())
        pitch_eff = np.append(pitch_eff, df_list[0]['pitch_eff'].tolist())
        yaw_eff = np.append(yaw_eff, df_list[0]['yaw_eff'].tolist())

    data = cmd_af

    # Remove unwanted characters and split the string into individual numbers
    cleaned_data = [row.replace('[', '').replace(']', '').split() for row in data]

    # Convert strings to floats in the 2D list
    float_data = [[float(entry) for entry in row] for row in cleaned_data]

    # Convert the 2D list to a NumPy array
    cmd_af = np.array(float_data)

    return np.array(doublet_actuator), np.array(airspeed), np.array(wing_angle),np.array(cmd_af), np.array(roll_eff), np.array(pitch_eff), np.array(yaw_eff)