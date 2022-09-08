function visualize_3d(ac_data, trange)

if ~isfield(ac_data, 'AHRS_REF_QUAT')
    return
end

msg = ac_data.AHRS_REF_QUAT;

quat = double([msg.body_qi msg.body_qx msg.body_qy msg.body_qz]);
refquat = double([msg.ref_qi msg.ref_qx msg.ref_qy msg.ref_qz]);
[refquat_t,irefquat_t,~] = unique(msg.timestamp);
quat = quat(irefquat_t,:);
refquat = refquat(irefquat_t,:);

offset = [90 0 0]/180*pi;
offset_quat = angle2quat(offset(1),offset(2),offset(3));

quat = quatmultiply(quat,offset_quat);

[psi, theta, phi] = quat2angle(quat,'ZYX');
[refpsi, reftheta, refphi] = quat2angle(refquat,'ZYX');

%% Example script to visualize the aircraft simulation data
% Add the path of the aircraft_3d_animation function
addpath('../ext/aircraft_3d_animation/src/');
% path of the *.mat file containing the 3d model information
% model_info_file = '../ext/aircraft_3d_animation/3d_models/saab_gripen_3d_model.mat';
model_info_file = '../ext/aircraft_3d_animation/3d_models/Nederdrone5.mat';

% define the reproduction speed factor
speedx = 1; 
% Do you want to save the animation in a mp4 file? (0.No, 1.Yes)
isave_movie = 0;
% Movie file name
movie_file_name = '';

% -------------------------------------------------------------------------
% The frame sample time shall be higher than 0.02 seconds to be able to 
% update the figure (CPU/GPU constraints)
% frame_sample_time = max(0.02, tout(2)-tout(1));
frame_sample_time = 0.02;
% Resample the time vector to modify the reproduction speed
dt_data = mean(diff(refquat_t));
if dt_data < 0.02
    factor = ceil(0.02/dt_data);
    t = refquat_t(1:factor:end);
    psi = psi(1:factor:end);
    theta = theta(1:factor:end);
    phi = phi(1:factor:end);
end

%datarange
dr_start = find(t>trange(1),1,'first')-1;
dr_stop = find(t>trange(2),1,'first')-1;
dr = dr_start:dr_stop; % valid for all signals of the same frequency

tdr = t(dr);
psi = psi(dr);
theta = theta(dr);
phi = phi(dr);


% t_new   = tout(1):frame_sample_time*(speedx):tout(end);
% Resample the recorded data

% Assign the data
heading_deg           =  psi/pi*180;
pitch_deg             =  theta/pi*180;
bank_deg              =  phi/pi*180;
roll_command          = zeros(size(phi));
pitch_command         = zeros(size(phi));
angle_of_attack_deg   =  zeros(size(phi)) * 180 / pi;
angle_of_sideslip_deg =  zeros(size(phi)) * 180 / pi;
fligh_path_angle_deg  =  zeros(size(phi)) * 180 / pi;
mach                  =  zeros(size(phi));
altitude_ft           =  zeros(size(phi));
nz_g                  =  zeros(size(phi));
% Flight control surfaces
% le     = act(:, 9);
% dr     = act(:, 8);
% df1    = act(:, 6);
% df2    = act(:, 5);
% df3    = act(:, 4);
% df4    = act(:, 3);
% dfp    = 0.5 * (act(:, 1) + act(:, 2));
% Control array assignation
% (modify the order according to your particular 3D model)
% controls_deflection_deg = [dfp(:), dfp(:), le(:), le(:), dr(:), 0.5*(df1(:)+df2(:)), 0.5*(df3(:)+df4(:))];
controls_deflection_deg = [zeros(length(phi),7)];

%% Run aircraft_3d_animation function
% -------------------------------------------------------------------------
aircraft_3d_animation(model_info_file,...
    heading_deg, ...            Heading angle [deg]
    pitch_deg, ...              Pitch angle [deg]
    bank_deg, ...               Roll angle [deg]
    roll_command, ...           Roll  stick command [-1,+1] [-1 -> left,            +1 -> right]
    pitch_command, ...          Pitch stick command [-1,+1] [-1 -> full-back stick, +1 -> full-fwd stick]
    angle_of_attack_deg, ...    AoA [deg]
    angle_of_sideslip_deg, ...  AoS [deg]
    fligh_path_angle_deg, ...   Flight path angle [deg]
    mach, ...                   Mach number
    altitude_ft, ...            Altitude [ft]
    nz_g,  ...                  Vertical load factor [g]
    controls_deflection_deg, ...Flight control deflection (each column is a control surface)
    frame_sample_time, ...      Sample time [sec]
    speedx, ...                 Reproduction speed
    isave_movie, ...            Save the movie? 0-1
    movie_file_name);           % Movie file name

end