function convert3Dmodel(MatFileName, rigid_body_list, rigid_body_colors, alphas, offset_3d_model, rotation_3d_model, ControlsFieldNames, Controls)

% Definition of the Model3D data structure
% Rigid body parts
rotation_3d_model = rotation_3d_model/180*pi;
Model3D.AircraftRot = angle2quat(rotation_3d_model(1),rotation_3d_model(2),rotation_3d_model(3));
for i = 1:length(rigid_body_list)
    Model3D.Aircraft(i).model = rigid_body_list{i};
    Model3D.Aircraft(i).color = rigid_body_colors{i};
    Model3D.Aircraft(i).alpha = alphas(i);
    % Read the *.stl file
   [Model3D.Aircraft(i).stl_data.vertices, Model3D.Aircraft(i).stl_data.faces, ~, Model3D.Aircraft(i).label] = stlRead(rigid_body_list{i});
    Model3D.Aircraft(i).stl_data.vertices  = Model3D.Aircraft(i).stl_data.vertices - offset_3d_model;
end
% Controls parts
Model3D.Control = [];
for i = 1:size(Controls, 1)
    for j = 1:size(Controls, 2)
        Model3D.Control(i).(ControlsFieldNames{j}) = Controls{i, j};
    end
    
    % Read the *.stl file
    [Model3D.Control(i).stl_data.vertices, Model3D.Control(i).stl_data.faces, ~, ~] = stlRead( Model3D.Control(i).model);
    Model3D.Control(i).stl_data.vertices = Model3D.Control(i).stl_data.vertices - offset_3d_model + Model3D.Control(i).position;
end
% Find all the parents
for i = 1:size(Controls, 1)
    Model3D.Control(i).parent_id = 0;
    for j = 1:size(Controls, 1)
        if strcmp(Model3D.Control(i).parent, Model3D.Control(j).label)
            Model3D.Control(i).parent_id = j;
            break
        end
    end
end

%% Save mat file
save(MatFileName, 'Model3D');

end