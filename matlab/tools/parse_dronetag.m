function data = parse_dronetag(json_file)
% PARSE_DRONETAG Parse dronetag JSON log file
%
% data = parse_dronetag(json_file)
%
% Inputs:
%   json_file - Path to dronetag JSON log file
%
% Outputs:
%   data - Struct containing parsed dronetag data with fields:
%          .time              - Time array (datetime)
%          .time_sec          - Time in seconds from start (double array)
%          .lat               - Latitude (double array)
%          .lon               - Longitude (double array)
%          .velocity_x        - Velocity X component (double array)
%          .velocity_y        - Velocity Y component (double array)
%          .velocity_z        - Velocity Z component (double array)
%          .velocity_mag      - Velocity magnitude (double array)
%          .altitude          - Altitude (double array)
%          .geo_altitude      - Geometric altitude (double array)
%          .vertical_accuracy - Vertical accuracy (double array)
%          .horizontal_accuracy - Horizontal accuracy (double array)
%          .speed_accuracy    - Speed accuracy (double array)
%          .height            - Height (double array)
%          .pressure          - Pressure (double array)
%
% Example:
%   data = parse_dronetag('flight_export_89e6deb4-fe0f-4287-9078-c54d63061366.json');
%   plot(data.time_sec, data.altitude);

    % Check if file exists
    if ~exist(json_file, 'file')
        error('File not found: %s', json_file);
    end
    
    % Read JSON file
    fprintf('Reading JSON file: %s\n', json_file);
    json_text = fileread(json_file);
    json_data = jsondecode(json_text);
    
    % Get number of data points
    n = length(json_data);
    fprintf('Found %d data points\n', n);
    
    % Preallocate arrays
    time_str = cell(n, 1);
    lat = zeros(n, 1);
    lon = zeros(n, 1);
    vel_x = zeros(n, 1);
    vel_y = zeros(n, 1);
    vel_z = zeros(n, 1);
    altitude = zeros(n, 1);
    geo_altitude = zeros(n, 1);
    vertical_accuracy = zeros(n, 1);
    horizontal_accuracy = zeros(n, 1);
    speed_accuracy = zeros(n, 1);
    height = zeros(n, 1);
    pressure = zeros(n, 1);
    
    % Extract data from JSON
    for i = 1:n
        entry = json_data(i);
        
        % Time
        time_str{i} = entry.time;
        
        % Location
        lat(i) = entry.location.lat;
        lon(i) = entry.location.lon;
        
        % Velocity
        vel_x(i) = entry.velocity.x;
        vel_y(i) = entry.velocity.y;
        vel_z(i) = entry.velocity.z;
        
        % Altitude and related fields
        altitude(i) = entry.altitude;
        geo_altitude(i) = entry.geo_altitude;
        height(i) = get_scalar(entry, "height");
        
        % Accuracy fields
        vertical_accuracy(i) = entry.vertical_accuracy;
        horizontal_accuracy(i) = entry.horizontal_accuracy;
        
        % Speed accuracy can be null
        if ~isempty(entry.speed_accuracy) && ~isnan(entry.speed_accuracy)
            speed_accuracy(i) = entry.speed_accuracy;
        else
            speed_accuracy(i) = NaN;
        end
        
        % Pressure
        pressure(i) = get_scalar(entry, "pressure");
    end
    
    % Normalize timestamps before parsing:
    %   - Strip trailing 'Z' (avoid conflicts with TimeZone parameter)
    %   - Truncate fractional seconds to exactly 3 digits (ms)
    %   - Add '.000' to timestamps that have no fractional part
    time_str = regexprep(time_str, 'Z$', '');              % strip Z
    time_str = regexprep(time_str, '(\.\d{3})\d+', '$1'); % truncate to ms
    time_str = regexprep(time_str, '(:\d{2})$', '$1.000'); % add .000 only when no fractional part yet
    
    % Convert time strings to datetime
    time = datetime(time_str, 'InputFormat', 'yyyy-MM-dd''T''HH:mm:ss.SSS', 'TimeZone', 'UTC');
    
    % Calculate time in seconds from start
    time_sec = seconds(time - time(1));
    
    % Calculate velocity magnitude
    velocity_mag = sqrt(vel_x.^2 + vel_y.^2 + vel_z.^2);
    
    % Create output structure
    data = struct();
    data.time = time;
    data.time_sec = time_sec;
    data.lat = lat;
    data.lon = lon;
    data.velocity_x = vel_x;
    data.velocity_y = vel_y;
    data.velocity_z = vel_z;
    data.velocity_mag = velocity_mag;
    data.altitude = altitude;
    data.geo_altitude = geo_altitude;
    data.vertical_accuracy = vertical_accuracy;
    data.horizontal_accuracy = horizontal_accuracy;
    data.speed_accuracy = speed_accuracy;
    data.height = height;
    data.pressure = pressure;
    
    fprintf('Successfully parsed dronetag data\n');
    fprintf('Time range: %s to %s (%.1f seconds)\n', ...
        format_time(time(1)), format_time(time(end)), time_sec(end));
    fprintf('Altitude range: %.2f to %.2f m\n', min(altitude), max(altitude));
    fprintf('Max velocity: %.2f m/s\n', max(velocity_mag));
    
end

function value = get_scalar(entry, field_name)
    value = NaN;
    if ~isfield(entry, field_name)
        return;
    end

    field_value = entry.(field_name);
    if isempty(field_value)
        return;
    end

    if isnumeric(field_value)
        if isscalar(field_value)
            value = field_value;
        end
        return;
    end

    if ischar(field_value) || isstring(field_value)
        numeric_value = str2double(field_value);
        if ~isnan(numeric_value)
            value = numeric_value;
        end
    end
end

function text = format_time(value)
    if ismissing(value) || isnat(value)
        text = 'NaT';
        return;
    end

    text = char(datetime(value, 'Format', 'yyyy-MM-dd HH:mm:ss.SSS'));
end
