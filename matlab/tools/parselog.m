function s = parselog(filename, msgs, whitelist)

% Check if the data file exists
[filepath, name, ext] = fileparts(filename);
if isempty(ext)
    filename = strcat(filepath, filesep, name, '.data');
end

% Determine which filename to use for log lookup (original name for filtered files)
log_lookup_filename = filename;
filtered_suffix = '_filtered';
if endsWith(name, filtered_suffix)
    base_name = name(1:end-length(filtered_suffix));
    log_lookup_filename = fullfile(filepath, [base_name '.data']);
end

if exist(filename, 'file') ~= 2
    error("The log file does not exist '%s'", filename)
end

% If whitelist parameter is provided, filter the data file first
if nargin >= 3
    original_filename = filename;
    filtered_filename = strcat(filepath, filesep, name, '_filtered.data');
    
    % Build whitelist string (empty if whitelist is empty)
    if isempty(whitelist)
        whitelist_str = '';
    else
        whitelist_str = sprintf('"%s" ', whitelist{:});
    end
    
    py_script = fullfile(filepath, 'tools', 'filter_datafile.py');
    if ~exist(py_script, 'file')
        py_script = 'tools/filter_datafile.py'; % fallback to relative path
    end
    cmd = sprintf('python3 "%s" "%s" "%s" %s', py_script, filename, filtered_filename, whitelist_str);
    
    if isempty(whitelist)
        fprintf('Filtering data file (pruning pre-motors_on data only)...\n');
    else
        fprintf('Filtering data file with whitelist...\n');
    end
    
    status = system(cmd);
    if status ~= 0
        error('Filtering data file failed.');
    end
    if exist(filtered_filename, 'file') ~= 2
        error('Filtered data file was not created: %s', filtered_filename);
    end
    filename = filtered_filename;
    log_lookup_filename = original_filename;
end

% Parse messages and settings from log file
got_log = false;
if nargin < 2 || isempty(msgs)
    % Parse messages
    l = splitlog(log_lookup_filename);
    if ~isempty(l.msgs)
        msgs = messages(l.msgs);
        got_log = true;
    else
        warning('Parsing log file with only the .data file, which could lead to unexpected message fields.')
        msgs = messages();
    end
end

% Parse settings from log file (with XML generation)
settings_info_all = parse_settings(log_lookup_filename, true);

if isfield(settings_info_all, 'aircrafts') && ~isempty(settings_info_all.aircrafts)
    got_log = true;
end

s.msgs = msgs;
s.settings_info = settings_info_all;

% Read everything as timestamp, A/C ID, msg name and msg contents
fid = fopen(filename);
    if fid == -1
        error('Could not open file: %s', filename);
    end
    C = textscan(fid, '%f %u %s %[^\n]');    
    fclose(fid);
    timestamp = C{1};
    aircraftID = C{2};
    msgName = C{3};
    msgContent = C{4};

% Check if it is a valid log file
if isempty(timestamp)
    error ('File %s can not be parsed as a paparazzi data file.', filename);
end

% Find all unqiue aircrafts from the log file
uniqueAC = unique(aircraftID);
nAC = size(uniqueAC, 1);
uniqueMsg = unique(msgName);

% Go through all the AC in the log
for iAC = nAC:-1:1 % counting backwards eliminates preallocation
    ac_id = uniqueAC(iAC);
    msg_ids = (aircraftID == ac_id);
    
    % Set the AC_ID and parse the data from the log for that AC
    s.aircrafts(iAC).AC_ID = ac_id;
    
    % Get settings info if available (combines generic + aircraft-specific)
    settings_info = struct();
    if got_log
        % Start with generic settings if they exist
        if isfield(settings_info_all, 'generic_settings_map')
            % Copy all generic settings
            generic_settings = settings_info_all.generic_settings_map;
            gen_keys = fieldnames(generic_settings);
            for iKey = 1:length(gen_keys)
                settings_info.(gen_keys{iKey}) = generic_settings.(gen_keys{iKey});
            end
        end
        
        % Add aircraft-specific settings if available
        if isfield(settings_info_all, 'aircrafts')
            aircrafts_struct = settings_info_all.aircrafts;
            % Find aircraft with matching AC_ID
            for iAC_idx = 1:length(aircrafts_struct)
                if aircrafts_struct(iAC_idx).AC_ID == ac_id
                    if isfield(aircrafts_struct(iAC_idx), 'settings_map')
                        % Merge with generic settings (aircraft-specific override generic)
                        ac_settings = aircrafts_struct(iAC_idx).settings_map;
                        ac_setting_keys = fieldnames(ac_settings);
                        for iKey = 1:length(ac_setting_keys)
                            settings_info.(ac_setting_keys{iKey}) = ac_settings.(ac_setting_keys{iKey});
                        end
                    end
                    break;
                end
            end
        end
    end
    
        [s.aircrafts(iAC).data, s.aircrafts(iAC).settings, s.aircrafts(iAC).settings_changes] = parse_aircraft_data(msgs, uniqueMsg, timestamp(msg_ids), msgName(msg_ids), msgContent(msg_ids), settings_info, ac_id);
    if isfield(settings_info_all, "aircrafts")
        try
            if ac_id <= length(settings_info_all.aircrafts) && isfield(settings_info_all.aircrafts(ac_id), "name")
                s.aircrafts(iAC).name = settings_info_all.aircrafts(ac_id).name;
            end
        catch
            % No aircraft name available from settings
        end
    end
    
    % Additional info from the data
    ac_data = s.aircrafts(iAC).data;
    if isfield(ac_data, "AUTOPILOT_VERSION")
        s.aircrafts(iAC).version = ac_data.AUTOPILOT_VERSION.version(1);
        s.aircrafts(iAC).version_desc = string(ac_data.AUTOPILOT_VERSION.desc(1));
    end
    if isfield(ac_data, "ROTORCRAFT_STATUS")
        s.aircrafts(iAC).in_flight = ac_data.ROTORCRAFT_STATUS.ts(find(diff(int32(ac_data.ROTORCRAFT_STATUS.ap_in_flight)))+1);
        s.aircrafts(iAC).motors_on = ac_data.ROTORCRAFT_STATUS.ts(find(diff(int32(ac_data.ROTORCRAFT_STATUS.ap_motors_on)))+1);
    end
end

end

% Parse the log lines from a specific aircraft
function [s, settings_tracker, settings_changes] = parse_aircraft_data(msgs, uniqueMsg, timestamp, msgName, msgContent, settings_info, ac_id)
    nMsg = size(uniqueMsg, 1);
    
    % Initialize settings tracking structure
    settings_tracker = struct();
    settings_changes = struct('msg_id', [], 'timestamp', [], 'old_value', [], 'new_value', []);
    
    % Build reverse map from index to field_name for fast lookup
    index_to_fieldname = containers.Map('KeyType', 'uint32', 'ValueType', 'char');
    
    % Pre-populate tracker with settings from settings_info
    if ~isempty(fieldnames(settings_info))
        settings_indices = fieldnames(settings_info);
        for iSetting = 1:length(settings_indices)
            setting_key = settings_indices{iSetting};
            setting_struct = settings_info.(setting_key);
            
            % Use idx-based key to preserve duplicate var names
            setting_id = setting_key;
            
            setting_name = '';
            setting_var = '';
            if isfield(setting_struct, 'name')
                setting_name = setting_struct.name;
            end
            if isfield(setting_struct, 'var')
                setting_var = setting_struct.var;
            end
            
            % Initialize with placeholder (will be overwritten when messages are found)
            if ~isfield(settings_tracker, setting_id)
                if isfield(setting_struct, 'index')
                    settings_tracker.(setting_id).index = setting_struct.index;
                    % Build reverse map: index -> field_name
                    idx_key = uint32(setting_struct.index);
                    index_to_fieldname(idx_key) = setting_id;
                end
                if ~isempty(setting_var)
                    settings_tracker.(setting_id).var = setting_var;
                end
                if ~isempty(setting_name)
                    settings_tracker.(setting_id).name = setting_name;
                end
                settings_tracker.(setting_id).original_value = [];
                settings_tracker.(setting_id).changes = struct('timestamp', [], 'value', []);
            end
        end
    end
    
    % Identify which messages might contain setting changes
    % Common names: DL_SETTING, SETTING, DL_VALUE, DL_VALUES
    setting_msg_candidates = uniqueMsg(contains(uniqueMsg, 'SETTING', 'IgnoreCase', true) | strcmpi(uniqueMsg, 'DL_VALUE') | strcmpi(uniqueMsg, 'DL_VALUES'));

    % Go through all messages
    for iMsg = 1:nMsg
        msg_name = uniqueMsg{iMsg};
        
        % Check in which message class the message is
        if isfield(msgs.telemetry, msg_name)
            msg_info = msgs.telemetry.(msg_name);
        elseif isfield(msgs.ground, msg_name)
            msg_info = msgs.ground.(msg_name);
        elseif isfield(msgs.datalink, msg_name)
            msg_info = msgs.datalink.(msg_name);
        elseif isfield(msgs.alert, msg_name)
            msg_info = msgs.alert.(msg_name);
        elseif isfield(msgs.imcu, msg_name)
            msg_info = msgs.imcu.(msg_name);
        end
            
        % Get message fields and ids
        msg_fields = msg_info.field_names;
        msg_ids = strcmp(msg_name, msgName);

        % Set the timestamp and parse the content from the XML heads
        s.(msg_name).ts = timestamp(msg_ids);      
        nFields = size(msg_fields, 2);
        msg_lines = msgContent(msg_ids);
        
        % Only parse content if needed
        if nFields > 0
            msgContentStr = strjoin(msg_lines);
            field_parser = join(msg_info.field_parser, ' ');    
            content = textscan(msgContentStr, field_parser);
            
        end
        
        % Special handling for setting messages to track changes
        if any(strcmp(setting_msg_candidates, msg_name)) && nFields > 0
            [settings_tracker, settings_changes] = track_setting_changes(settings_tracker, settings_changes, timestamp(msg_ids), content, msg_info, settings_info, index_to_fieldname, ac_id);
        end
        
        % Go through all the fields in the messages
        for j = 1:nFields
            field_name = msg_fields(j);
            field_isarray = msg_info.field_isarray(j);
            values = content{j};
            
            if field_isarray
                try
                    s.(msg_name).(field_name) = split(values, ",");
                catch
                    disp("Message " + msg_name + " has unequal data length lines, not putting in array.")
                    s.(msg_name).(field_name) = values;
                end
            else
                s.(msg_name).(field_name) = values;
            end
            
            % Parse alternate unit
            field_info = msg_info.fields.(field_name);
            if field_info.alt_unit_coef ~= 1 && field_isarray
               s.(msg_name).(strcat(field_name, "_alt")) = double(string(s.(msg_name).(field_name))) .* field_info.alt_unit_coef;
            elseif field_info.alt_unit_coef ~= 1
               s.(msg_name).(strcat(field_name, "_alt")) = double(values) .* field_info.alt_unit_coef;
            end
        end
        
        % Convert numeric cell arrays to double arrays after all fields are parsed
        for j = 1:nFields
            field_name = msg_fields(j);
            field_isarray = msg_info.field_isarray(j);
            
            if field_isarray && isfield(s.(msg_name), field_name)
                values = s.(msg_name).(field_name);
                
                % Try converting to numeric using double(string())
                try
                    numeric_array = double(string(values));
                    
                    % Check if conversion succeeded (no NaN from non-numeric strings)
                    if ~all(isnan(numeric_array(:)))
                        s.(msg_name).(field_name) = numeric_array;
                    end
                catch
                    % Keep as cell array if conversion fails
                end
            end
        end
    end
    
    % Remove internal fields
    if ~isempty(fieldnames(settings_tracker))
        setting_names = fieldnames(settings_tracker);
        for iSetting = 1:length(setting_names)
            if isfield(settings_tracker.(setting_names{iSetting}), 'last_value')
                settings_tracker.(setting_names{iSetting}) = rmfield(settings_tracker.(setting_names{iSetting}), 'last_value');
            end
        end
    end
end

% Track setting changes for setting messages
function [tracker, change_list] = track_setting_changes(tracker, change_list, timestamps, content, msg_info, settings_info, index_to_fieldname, current_ac_id)
    % Find the setting identifier field (typically 'index' or 'ac_id')
    field_names = msg_info.field_names;
    
    % Find indices for common field names
    idx_field_idx = find(strcmp(field_names, 'index'));
    ac_id_field_idx = find(strcmp(field_names, 'ac_id'));
    value_field_idx = find(strcmp(field_names, 'value'));
    
    if isempty(idx_field_idx) || isempty(value_field_idx)
        return; % Cannot track without index and value fields
    end
    
    setting_indices = content{idx_field_idx};
    setting_values = content{value_field_idx};
    
    % Get ac_ids if available for filtering
    setting_ac_ids = [];
    if ~isempty(ac_id_field_idx)
        setting_ac_ids = content{ac_id_field_idx};
    end
    
    % Process each setting change
    for i = 1:length(timestamps)
        % Skip if this SETTING message is not for the current aircraft
        if ~isempty(setting_ac_ids) && nargin >= 8 && setting_ac_ids(i) ~= current_ac_id
            continue;
        end
        
        idx = setting_indices(i);
        idx_adjusted = idx;
        current_value = setting_values(i);
        
        
        % Use the index_to_fieldname map to find the field_name for this index
        setting_id = '';
        setting_var = '';
        setting_name = '';
        
        if nargin >= 7 && ~isempty(index_to_fieldname) && isKey(index_to_fieldname, uint32(idx))
            setting_id = index_to_fieldname(uint32(idx));
        elseif nargin >= 7 && ~isempty(index_to_fieldname) && idx > 0 && isKey(index_to_fieldname, uint32(idx - 1))
            % Fallback for 1-based indices in log messages
            idx_adjusted = idx - 1;
            setting_id = index_to_fieldname(uint32(idx_adjusted));
            % Retrieve var and name from the tracker if already populated
            if isfield(tracker, setting_id)
                if isfield(tracker.(setting_id), 'var')
                    setting_var = tracker.(setting_id).var;
                end
                if isfield(tracker.(setting_id), 'name')
                    setting_name = tracker.(setting_id).name;
                end
            end
        else
            % Fallback: search through settings_info for matching index
            if ~isempty(fieldnames(settings_info))
                settings_keys = fieldnames(settings_info);
                for iKey = 1:length(settings_keys)
                    test_struct = settings_info.(settings_keys{iKey});
                    if isfield(test_struct, 'index') && test_struct.index == idx
                        setting_var = '';
                        if isfield(test_struct, 'var')
                            setting_var = test_struct.var;
                        end
                        if isfield(test_struct, 'name')
                            setting_name = test_struct.name;
                        end
                        % Use idx-based key to preserve duplicates
                        setting_id = settings_keys{iKey};
                        break;
                    elseif isfield(test_struct, 'index') && test_struct.index == (idx - 1)
                        setting_var = '';
                        if isfield(test_struct, 'var')
                            setting_var = test_struct.var;
                        end
                        if isfield(test_struct, 'name')
                            setting_name = test_struct.name;
                        end
                        idx_adjusted = idx - 1;
                        setting_id = settings_keys{iKey};
                        break;
                    end
                end
            end
        end
        
        if ~isempty(setting_id)
            current_value = setting_values(i);
            if ~isfield(tracker, setting_id)
                % First occurrence - store as original value
                tracker.(setting_id).index = idx_adjusted;
                if ~isempty(setting_var)
                    tracker.(setting_id).var = setting_var;
                end
                if ~isempty(setting_name)
                    tracker.(setting_id).name = setting_name;
                end
                tracker.(setting_id).original_value = current_value;
                tracker.(setting_id).changes = struct('timestamp', [], 'value', []);
                tracker.(setting_id).last_value = current_value;
            else
                % Ensure original_value is set if pre-populated
                if ~isfield(tracker.(setting_id), 'original_value') || isempty(tracker.(setting_id).original_value)
                    tracker.(setting_id).original_value = current_value;
                end
                % Compare against last_value and log changes with timestamp
                if ~isfield(tracker.(setting_id), 'last_value')
                    tracker.(setting_id).last_value = tracker.(setting_id).original_value;
                end
                if ~isfield(tracker.(setting_id), 'changes') || ~isstruct(tracker.(setting_id).changes)
                    tracker.(setting_id).changes = struct('timestamp', [], 'value', []);
                end
                if ~isequal(tracker.(setting_id).last_value, current_value)
                    old_value = tracker.(setting_id).last_value;
                    tracker.(setting_id).changes.timestamp(end+1, 1) = timestamps(i);
                    tracker.(setting_id).changes.value(end+1, 1) = current_value;
                    change_list.msg_id(end+1, 1) = idx_adjusted;
                    change_list.timestamp(end+1, 1) = timestamps(i);
                    change_list.old_value(end+1, 1) = old_value;
                    change_list.new_value(end+1, 1) = current_value;
                    tracker.(setting_id).last_value = current_value;
                end
            end
        end
    end
end

% Extract message protocol from the .log file
function s = splitlog(filename, gen_files)
    % Extract protocol section from log file
    [filepath, name,] = fileparts(filename);
    log_filename = strcat(filepath, filesep, name, '.log');
    
    % Check if the log file exists
    s.msgs = '';
    if exist(log_filename, 'file') ~= 2
        return
    end
    
    % Do not create aircraft files by default
    if nargin < 2
        gen_files = false;
    end
    
    % Initial setup
    flog = fopen(log_filename, 'rt');
    got_prot = false;
    
    % Go through the log file and extract protocol
    while 1
         tline = fgetl(flog);
         
         % When it is the end of the file stop
         if ~ischar(tline)
             break
         end
         
         % Check for protocol start
         if contains(tline, '<protocol')
             % Create messages XML file with protocol
             s.msgs = strcat(filepath, filesep, name, '_msgs.xml');
             fmsgs = fopen(s.msgs, 'w');
             got_prot = true;
         end
         
         % Write protocol lines to file
         if got_prot
             fprintf(fmsgs, "%s\n", tline);
         end
         
         % Check for protocol end
         if got_prot && contains(tline, '</protocol>')
             got_prot = false;
             fclose(fmsgs);
             break;
         end
    end
    
    % Close the log file
    fclose(flog);
end
