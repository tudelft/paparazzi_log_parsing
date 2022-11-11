function visualize_3d(model_name, ac_data, trange)
    if ~isfield(ac_data, 'AHRS_REF_QUAT')
        return
    end

    % Add the path of the aircraft_3d_animation function
    addpath('3d_animation');
    % path of the *.mat file containing the 3d model information
    model_info_file = strcat('3d_animation/3d_models/',model_name,'.mat');

    % define the reproduction speed factor
    speedx = 1;

    % -------------------------------------------------------------------------
    % Read AHRS_REF_QUAT
    quat = double([ac_data.AHRS_REF_QUAT.body_qi ac_data.AHRS_REF_QUAT.body_qx ac_data.AHRS_REF_QUAT.body_qy ac_data.AHRS_REF_QUAT.body_qz]);
    quat_ref = double([ac_data.AHRS_REF_QUAT.ref_qi ac_data.AHRS_REF_QUAT.ref_qx ac_data.AHRS_REF_QUAT.ref_qy ac_data.AHRS_REF_QUAT.ref_qz]);
    [quat_t,iquat_t,~] = unique(ac_data.AHRS_REF_QUAT.timestamp);
    quat = quat(iquat_t,:);
    quat_ref = quat_ref(iquat_t,:);
    
    % -------------------------------------------------------------------------
    % RESAMPLE
    % The frame sample time shall be higher than 15fps to be able to 
    % update the figure (CPU/GPU constraints)
    dt_data = mean(diff(quat_t));
    frame_sample_time = max(1/15, dt_data);
   
    % Calculate data range
    t_new   = quat_t(1):frame_sample_time*(speedx):quat_t(end);
    dr_start = find(t_new>trange(1),1,'first');
    dr_stop = find(t_new>trange(2),1,'first');
    dr = dr_start:dr_stop; % valid for all signals of the same frequency
    
    % Resample AHRS_REF_QUAT
    quat = interp1(quat_t, quat, t_new', 'linear');
    quat = quat(dr, :);
    quat_ref = interp1(quat_t, quat_ref, t_new', 'linear');
    quat_ref = quat_ref(dr, :);
    
    % Resample and parse ACTUATORS
    actuators = [];
    if isfield(ac_data, 'ACTUATORS')
        actuators = double(string(ac_data.ACTUATORS.values));
        [act_t,iact_t,~] = unique(ac_data.ACTUATORS.timestamp);
        actuators = actuators(iact_t,:);
        actuators = interp1(act_t, actuators, t_new', 'linear');
        actuators = actuators(dr, :);
    end
    
    % Resample and parse AIR_DATA
    airspeed = [];
    if isfield(ac_data, 'AIR_DATA')
        [aird_t,iaird_t,~] = unique(ac_data.AIR_DATA.timestamp);
        airspeed = ac_data.AIR_DATA.airspeed(iaird_t,:);
        airspeed = interp1(aird_t, airspeed, t_new', 'linear');
        airspeed = airspeed(dr, :);
    end
    
    % Resample and parse ROTORCRAFT_FP
    altitude_m = [];
    if isfield(ac_data, 'ROTORCRAFT_FP')
        [rotfp_t,irotfp_t,~] = unique(ac_data.ROTORCRAFT_FP.timestamp);
        altitude_m = ac_data.ROTORCRAFT_FP.up_alt(irotfp_t,:);
        altitude_m = interp1(rotfp_t, altitude_m, t_new', 'linear');
        altitude_m = altitude_m(dr, :);
    end
    
    % Resample and parse RC commands
    rc_commands = [];
    
    % Resample and parse AoA
    angle_of_attack_deg = [];
    angle_of_sideslip_deg = [];


    %% Run aircraft_3d_animation function
    % -------------------------------------------------------------------------
    pprz_3d_animation(model_info_file,...
        quat, ...                   Quaternion of the plane
        quat_ref, ...               Reference quaternion of the plane
        actuators, ...              Actuator values [airframe min, airframe max]
        rc_commands, ...            RC commands (Thrust, Roll, Pitch, Yaw) [-9600, +9600]
        angle_of_attack_deg, ...    AoA [deg]
        angle_of_sideslip_deg, ...  AoS [deg]
        airspeed, ...               Airspeed [m/s]
        altitude_m, ...             Altitude [m]
        frame_sample_time, ...      Sample time [sec]
        speedx);                    % Reproduction speed

end