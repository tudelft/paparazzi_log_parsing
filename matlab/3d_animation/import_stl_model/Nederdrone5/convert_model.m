clearvars;
close all;
% Add path to stlTools
addpath('../../stlTools');
addpath('../../');
% Set the name of the mat file containing all the info of the 3D model
MatFileName = '../../3d_models/Nederdrone5.mat';
% Define the list of parts which will be part of the rigid aircraft body
rigid_body_list   = {'Nederdrone No Flaps.stl'};
% Define the color of each part
rigid_body_colors = {0.8 * [1, 1, 1]};
% Define the transparency of each part
alphas            = [1];
% Define the model offset and rotation (deg) to center the A/C Center of Gravity
offset_3d_model   = [0,0,0];
rotation_3d_model = [-90,0,0];
% Define the controls 
ControlsFieldNames = {...
'index', 'model', 'label', 'parent', 'color', 'position', 'rot_point', 'rot_vect', 'max_deflection'};
Controls = {
  7,  'Left Front Wing Flap.stl',  'LFF', 'BODY', 0.3*[0.8, 0.8, 1], [0, 0, 0], [600,  -120,  360]-offset_3d_model, [-1, 0, 0], [-50, +50, -6000, 0, +6000];
  8,  'Right Front Wing Flap.stl', 'RFF', 'BODY', 0.3*[0.8, 0.8, 1], [0, 0, 0], [-600, -120,  360]-offset_3d_model, [1,  0, 0], [+50, -50, +6000, 0, -6000];
  15, 'Left Back Wing Flap.stl',   'LBF', 'BODY', 0.3*[0.8, 0.8, 1], [0, 0, 0], [600,    75, -230]-offset_3d_model, [-1, 0, 0], [-30, +30, -6000, 0, +6000];
  16, 'Right Back Wing Flap.stl',  'RBF', 'BODY', 0.3*[0.8, 0.8, 1], [0, 0, 0], [-600,   75, -230]-offset_3d_model, [1,  0, 0], [+30, -30, +6000, 0, -6000];
  
  1,  '15 inch prop Disc.stl',     'M1',  'LFF',  0.3*[0.8, 0.8, 1], [1104, -120, 290], [0, 0, 0]-offset_3d_model, [0,  0, 0], [0, 0, -8191, 1500, 8191];
  2,  '15 inch prop Disc.stl',     'M2',  'BODY', 0.3*[0.8, 0.8, 1], [733, -120, 590], [0, 0, 0]-offset_3d_model, [0,  0, 0], [0, 0, 0, 600, 8191];
  3,  '15 inch prop Disc.stl',     'M3',  'BODY', 0.3*[0.8, 0.8, 1], [318, -120, 590], [0, 0, 0]-offset_3d_model, [0,  0, 0], [0, 0, 0, 600, 8191];
  4,  '15 inch prop Disc.stl',     'M4',  'BODY', 0.3*[0.8, 0.8, 1], [-318, -120, 590], [0, 0, 0]-offset_3d_model, [0,  0, 0], [0, 0, 0, 600, 8191];
  5,  '15 inch prop Disc.stl',     'M5',  'BODY', 0.3*[0.8, 0.8, 1], [-733, -120, 590], [0, 0, 0]-offset_3d_model, [0,  0, 0], [0, 0, 0, 600, 8191];
  6,  '15 inch prop Disc.stl',     'M6',  'RFF',  0.3*[0.8, 0.8, 1], [-1104, -120, 290], [0, 0, 0]-offset_3d_model, [0,  0, 0], [0, 0, -8191, 1500, 8191];
  
  9,  '15 inch prop Disc.stl',     'M7',  'BODY', 0.3*[0.8, 0.8, 1], [1104, 75, 0], [0, 0, 0]-offset_3d_model, [0,  0, 0], [0, 0, 0, 600, 8191];
  10, '15 inch prop Disc.stl',     'M8',  'BODY', 0.3*[0.8, 0.8, 1], [733, 75, 0], [0, 0, 0]-offset_3d_model, [0,  0, 0], [0, 0, 0, 600, 8191];
  11, '15 inch prop Disc.stl',     'M9',  'BODY', 0.3*[0.8, 0.8, 1], [318, 75, 0], [0, 0, 0]-offset_3d_model, [0,  0, 0], [0, 0, 0, 600, 8191];
  12, '15 inch prop Disc.stl',     'M10', 'BODY', 0.3*[0.8, 0.8, 1], [-318, 75, 0], [0, 0, 0]-offset_3d_model, [0,  0, 0], [0, 0, 0, 600, 8191];
  13, '15 inch prop Disc.stl',     'M11', 'BODY', 0.3*[0.8, 0.8, 1], [-733, 75, 0], [0, 0, 0]-offset_3d_model, [0,  0, 0], [0, 0, 0, 600, 8191];
  14, '15 inch prop Disc.stl',     'M12', 'BODY', 0.3*[0.8, 0.8, 1], [-1104, 75, 0], [0, 0, 0]-offset_3d_model, [0,  0, 0], [0, 0, 0, 600, 8191];
};


%% Convert the model
convert3Dmodel(MatFileName, rigid_body_list, rigid_body_colors, alphas, offset_3d_model, rotation_3d_model, ControlsFieldNames, Controls)

%% Check the results
plot3Dmodel(MatFileName)