clearvars;
close all;
% Add path to stlTools
addpath('../../stlTools');
addpath('../../');
% Set the name of the mat file containing all the info of the 3D model
MatFileName = '../../3d_models/RotatingWingV3.mat';
% Define the list of parts which will be part of the rigid aircraft body
rigid_body_list   = {'RWV4 NonMoving_.stl','RudderSolid_.stl'};
% Define the color of each part
rigid_body_colors = {0.8 * [1, 1, 1], 0.8 * [1, 1, 1]};
% Define the transparency of each part
alphas            = [1, 1];
% Define the model offset and rotation (deg) to center the A/C Center of Gravity
offset_3d_model   = [0,680,0];
rotation_3d_model = [-90,0,0];
% Define the controls 
ControlsFieldNames = {...
'index', 'model', 'label', 'parent', 'color', 'position', 'rot_point', 'rot_vect', 'max_deflection'};
Controls = {
    1,  'WingQuad_.stl',  'WINGQ',  'BODY', 0.3*[0.8, 0.8, 1], [0, 0, 0], [0,  680,  0]-offset_3d_model, [0, 0, 1], [0, 90, -6000, 0, +6000];
    2,  'Rudder_.stl',    'RUDDER', 'BODY', 0.3*[0.8, 0.8, 1], [0, 0, 0], [8.3,  1486.6,  -32]-offset_3d_model, [-2, 0, -175], [-45, 45, -8191, 0, +8191];
    3,  'HoriTail_.stl',  'ELEV',   'BODY', 0.3*[0.8, 0.8, 1], [0, 0, 0], [0,  1430,  12.7]-offset_3d_model, [1, 0, 0], [-45, 45, -8191, 0, +8191];
    
    4,  'LeftAileron_.stl',  'LAIL',  'WINGQ', 0.3*[0.8, 0.8, 1], [0, 0, 0], [551,  734,  139]-offset_3d_model, [299, -16, 2], [-45, 45, -8191, 0, +8191];
    5,  'LeftFlap_.stl',     'LFLAP', 'WINGQ', 0.3*[0.8, 0.8, 1], [0, 0, 0], [200,  738,  140]-offset_3d_model, [350, -4, 0], [-45, 45, -8191, 0, +8191];
    6,  'RightAileron_.stl', 'RAIL',  'WINGQ', 0.3*[0.8, 0.8, 1], [0, 0, 0], [-551,  734,  139]-offset_3d_model, [-299, -16, 2], [-45, 45, -8191, 0, +8191];
    7,  'RightFlap_.stl',    'RFLAP', 'WINGQ', 0.3*[0.8, 0.8, 1], [0, 0, 0], [-200,  738,  140]-offset_3d_model, [-350, -4, 0], [-45, 45, -8191, 0, +8191];
    
    8,  '15 inch prop Disc_.stl',     'M1',  'BODY',  0.3*[0.8, 0.8, 1], [0,   232,  -124], [0, 0, 0]-offset_3d_model, [0, 0, 0], [0, 0, -8191, 1000, +8191];
    9,  '15 inch prop Disc_.stl',     'M2',  'BODY',  0.3*[0.8, 0.8, 1], [0,   1129, -124], [0, 0, 0]-offset_3d_model, [0,  0, 0], [0, 0, -8191, 1000, 8191];
    10, '15 inch prop Disc_.stl',     'M3',  'WINGQ', 0.3*[0.8, 0.8, 1], [-14, 310,  152], [0, 0, 0]-offset_3d_model, [0, 0, 0], [0, 0, -8191, 1000, +8191];
    11, '15 inch prop Disc_.stl',     'M4',  'WINGQ', 0.3*[0.8, 0.8, 1], [14,  1050, 152], [0, 0, 0]-offset_3d_model, [0,  0, 0], [0, 0, -8191, 1000, 8191];
};


%% Convert the model
convert3Dmodel(MatFileName, rigid_body_list, rigid_body_colors, alphas, offset_3d_model, rotation_3d_model, ControlsFieldNames, Controls)

%% Check the results
plot3Dmodel(MatFileName)