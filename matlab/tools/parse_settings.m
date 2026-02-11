function s = parse_settings(log_filename, gen_xml)
% Parse settings from a Paparazzi log file
% 
% Usage:
%   s = parse_settings(log_filename)           % Parse without generating XML
%   s = parse_settings(log_filename, true)     % Parse and generate XML file
%
% Output:
%   s.aircrafts(ac_id).settings_map     % Map of settings indexed by 'idx_N'
%   Each setting contains: var, index, name
%
% XML file is generated as: <log_name>_settings.xml (contains all aircraft)

if nargin < 2
    gen_xml = false;
end

% Parse the log filename
[filepath, name, ext] = fileparts(log_filename);
log_base_name = name;  % Preserve original log filename for XML output
log_filename_full = fullfile(filepath, [name '.log']);

% Check if the log file exists
s.aircrafts = struct();
if exist(log_filename_full, 'file') ~= 2
    warning("Log file does not exist '%s'", log_filename_full)
    return
end

% Pre-screen the .data file to find which aircraft are actually present (efficient shell-based approach)
present_ac_ids = containers.Map('KeyType', 'uint32', 'ValueType', 'logical');
data_filename = fullfile(filepath, [name '.data']);
if exist(data_filename, 'file') == 2
    % Use awk to extract unique AC_IDs efficiently
    [status, result] = system(sprintf('awk ''NR>1 && $2~/^[0-9]+$/ {print $2}'' "%s" | sort -u', data_filename));
    if status == 0 && ~isempty(strtrim(result))
        ac_ids_str = strsplit(strtrim(result), '\n');
        for i = 1:length(ac_ids_str)
            ac_id = str2double(ac_ids_str{i});
            if ~isnan(ac_id) && ac_id > 0
                present_ac_ids(uint32(ac_id)) = true;
            end
        end
    end
end

% Initialize
flog = fopen(log_filename_full, 'rt');
current_ac_id = 0;
current_ac_name = '';
current_ac_index = 0;  % Track which aircraft we're on (dense index)
current_settings_xml = {};
current_settings_seen = containers.Map('KeyType', 'char', 'ValueType', 'logical');
all_aircraft_xml = {};  % Collect all aircraft settings
in_settings = false;
in_generated_settings = false;
use_generated_settings = false;
settings_index_counter = struct();  % Per-aircraft counter
aircrafts_list = {};  % Cell array to track aircrafts (will convert to struct later)
group_stack = {};  % Track nested dl_settings groups

% Go through the log file
while 1
    tline = fgetl(flog);
    
    % End of file
    if ~ischar(tline)
        break
    end
    
    % Check for aircraft start
    if contains(tline, '<aircraft')
        % Save previous aircraft settings to collection if needed
        if current_ac_id > 0 && gen_xml && ~isempty(current_settings_xml) && isKey(present_ac_ids, uint32(current_ac_id))
            % Add aircraft header to collection
            all_aircraft_xml{end+1} = sprintf('  <aircraft name="%s" ac_id="%d">', current_ac_name, current_ac_id);
            all_aircraft_xml{end+1} = '    <settings>';
            % Add all settings for this aircraft
            for i = 1:length(current_settings_xml)
                all_aircraft_xml{end+1} = ['      ' current_settings_xml{i}];
            end
            all_aircraft_xml{end+1} = '    </settings>';
            all_aircraft_xml{end+1} = '  </aircraft>';
        end
        
        % Parse new aircraft using manual string extraction
        % Extract aircraft name
        name_start = strfind(tline, 'name="');
        if ~isempty(name_start)
            name_start = name_start(1) + 6;
            name_end = strfind(tline(name_start:end), '"') - 1;
            if ~isempty(name_end)
                current_ac_name = tline(name_start:name_start+name_end(1)-1);
            else
                current_ac_name = '';
            end
        else
            current_ac_name = '';
        end
        
        % Extract aircraft ID
        id_start = strfind(tline, 'ac_id="');
        if ~isempty(id_start)
            id_start = id_start(1) + 7;
            id_end = strfind(tline(id_start:end), '"') - 1;
            if ~isempty(id_end)
                id_str = tline(id_start:id_start+id_end(1)-1);
                current_ac_id = str2num(id_str);
            else
                current_ac_id = 0;
            end
        else
            current_ac_id = 0;
        end
        
        current_settings_xml = {};
        current_settings_seen = containers.Map('KeyType', 'char', 'ValueType', 'logical');
        in_settings = false;
        in_generated_settings = false;
        use_generated_settings = false;
        group_stack = {};
        
        % Initialize aircraft in struct (using dense indexing)
        current_ac_index = current_ac_index + 1;
        s.aircrafts(current_ac_index).AC_ID = current_ac_id;
        s.aircrafts(current_ac_index).name = current_ac_name;
        s.aircrafts(current_ac_index).settings_map = struct();
        
        % Store counter for this aircraft
        if ~isfield(settings_index_counter, sprintf('ac_%d', current_ac_id))
            settings_index_counter.(sprintf('ac_%d', current_ac_id)) = 0;
        end
        
    elseif contains(tline, '<generated_settings>')
        in_settings = true;
        in_generated_settings = true;
        group_stack = {};
        if ~use_generated_settings
            use_generated_settings = true;
            % Reset any previously collected settings for this aircraft
            if current_ac_index > 0
                s.aircrafts(current_ac_index).settings_map = struct();
                counter_key = sprintf('ac_%d', current_ac_id);
                settings_index_counter.(counter_key) = 0;
            end
            if gen_xml
                current_settings_xml = {};
                current_settings_seen = containers.Map('KeyType', 'char', 'ValueType', 'logical');
            end
        end
        
    elseif contains(tline, '<settings>')
        if ~use_generated_settings
            in_settings = true;
            group_stack = {};
        end
        
    elseif contains(tline, '</generated_settings>')
        in_settings = false;
        in_generated_settings = false;
        
    elseif contains(tline, '</settings>')
        if ~use_generated_settings
            in_settings = false;
        end
        
    elseif contains(tline, '<dl_settings') && in_settings
        if contains(tline, '</dl_settings')
            if ~isempty(group_stack)
                group_stack(end) = [];
            end
        else
            group_name = '';
            name_start = strfind(tline, 'name="');
            if isempty(name_start)
                name_start = strfind(tline, 'NAME="');
            end
            if isempty(name_start)
                name_start = strfind(tline, 'Name="');
            end
            if ~isempty(name_start)
                name_start = name_start(1) + 6;
                name_end = strfind(tline(name_start:end), '"') - 1;
                if ~isempty(name_end)
                    group_name = tline(name_start:name_start+name_end(1)-1);
                end
            end
            if ~isempty(group_name)
                group_stack{end+1} = group_name;
            end
        end

    elseif contains(tline, '</dl_settings>') && in_settings
        if ~isempty(group_stack)
            group_stack(end) = [];
        end

    elseif contains(tline, '<dl_setting') && in_settings
        % Add to current aircraft's XML buffer
        if ~isKey(current_settings_seen, tline)
            current_settings_xml{end+1} = tline;
            current_settings_seen(tline) = true;
        end
        
        % Parse setting information using manual string parsing (case-insensitive for attributes)
        % Extract var attribute (case-insensitive - try lowercase first, then uppercase)
        var_start = strfind(tline, 'var="');
        if isempty(var_start)
            var_start = strfind(tline, 'VAR="');
        end
        if isempty(var_start)
            var_start = strfind(tline, 'Var="');
        end
        
        var_name_raw = '';
        var_name = '';
        if ~isempty(var_start)
            var_start = var_start(1) + 5;
            var_end = strfind(tline(var_start:end), '"') - 1;
            if ~isempty(var_end)
                var_name_raw = tline(var_start:var_start+var_end(1)-1);
                % Remove array index suffix for the cleaned var name
                bracket_pos = strfind(var_name_raw, '[');
                if ~isempty(bracket_pos)
                    var_name = var_name_raw(1:bracket_pos(1)-1);
                else
                    var_name = var_name_raw;
                end
            end
        end
        
        % Extract explicit index attribute if present (preferred)
        idx = [];
        idx_start = strfind(tline, 'index="');
        if isempty(idx_start)
            idx_start = strfind(tline, 'INDEX="');
        end
        if isempty(idx_start)
            idx_start = strfind(tline, 'Index="');
        end
        if ~isempty(idx_start)
            idx_start = idx_start(1) + 7;
            idx_end = strfind(tline(idx_start:end), '"') - 1;
            if ~isempty(idx_end)
                idx_str = tline(idx_start:idx_start+idx_end(1)-1);
                idx = str2double(idx_str);
            end
        end
        
        % Extract shortname attribute (case-insensitive)
        name = var_name;  % Default to var name
        name_start = strfind(tline, 'shortname="');
        if isempty(name_start)
            name_start = strfind(tline, 'SHORTNAME="');
        end
        if isempty(name_start)
            name_start = strfind(tline, 'Shortname="');
        end
        
        if ~isempty(name_start)
            name_start = name_start(1) + 11;
            name_end = strfind(tline(name_start:end), '"') - 1;
            if ~isempty(name_end)
                name = tline(name_start:name_start+name_end(1)-1);
            end
        end
        
        % If no index found, use counter
        if isempty(idx)
            if current_ac_id > 0
                counter_key = sprintf('ac_%d', current_ac_id);
                if ~isfield(settings_index_counter, counter_key)
                    settings_index_counter.(counter_key) = 0;
                end
                idx = settings_index_counter.(counter_key);
                settings_index_counter.(counter_key) = settings_index_counter.(counter_key) + 1;
            else
                % Generic settings - use global counter
                if ~isfield(settings_index_counter, 'global')
                    settings_index_counter.global = 0;
                end
                idx = settings_index_counter.global;
                settings_index_counter.global = settings_index_counter.global + 1;
            end
        end
        
        % Store setting information - for both aircraft and generic settings
        % Use group + var-based keys for readability, disambiguate duplicates with index
        if ~isempty(idx)
            group_path = '';
            if ~isempty(group_stack)
                group_path = strjoin(group_stack, '/');
            end
            field_name_base_raw = var_name_raw;
            if ~isempty(group_path)
                field_name_base_raw = [group_path '_' var_name_raw];
            end
            field_name_base = strrep(field_name_base_raw, '.', '_');
            field_name_base = strrep(field_name_base, '/', '_');
            field_name_base = strrep(field_name_base, ' ', '_');
            field_name_base = strrep(field_name_base, '[', '_');
            field_name_base = strrep(field_name_base, ']', '');
            field_name_base = matlab.lang.makeValidName(field_name_base);
            field_name = field_name_base;
            skip_duplicate = false;
            
            % For aircraft-specific settings
            if current_ac_id > 0
                % Check if var name already exists; disambiguate with index
                if isfield(s.aircrafts(current_ac_index).settings_map, field_name)
                    field_name = sprintf('%s_idx_%d', field_name_base, idx);
                    field_name = matlab.lang.makeValidName(field_name);
                    if isfield(s.aircrafts(current_ac_index).settings_map, field_name)
                        skip_duplicate = true;
                    end
                end
                
                if ~skip_duplicate
                    s.aircrafts(current_ac_index).settings_map.(field_name).var = var_name_raw;
                    s.aircrafts(current_ac_index).settings_map.(field_name).index = idx;
                    s.aircrafts(current_ac_index).settings_map.(field_name).name = name;
                    if ~isempty(group_path)
                        s.aircrafts(current_ac_index).settings_map.(field_name).group = group_path;
                    end
                end
            else
                % For generic settings, store in a global settings_map
                if ~isfield(s, 'generic_settings_map')
                    s.generic_settings_map = struct();
                end
                
                % Check if var name already exists; disambiguate with index
                if isfield(s.generic_settings_map, field_name)
                    field_name = sprintf('%s_idx_%d', field_name_base, idx);
                    field_name = matlab.lang.makeValidName(field_name);
                    if isfield(s.generic_settings_map, field_name)
                        skip_duplicate = true;
                    end
                end
                
                if ~skip_duplicate
                    s.generic_settings_map.(field_name).var = var_name_raw;
                    s.generic_settings_map.(field_name).index = idx;
                    s.generic_settings_map.(field_name).name = name;
                    if ~isempty(group_path)
                        s.generic_settings_map.(field_name).group = group_path;
                    end
                end
            end
        end
        
    elseif in_settings
        % Other lines within settings block
        current_settings_xml{end+1} = tline;
    end
end

% Save final aircraft's settings to collection if needed
if current_ac_id > 0 && gen_xml && ~isempty(current_settings_xml) && isKey(present_ac_ids, uint32(current_ac_id))
    all_aircraft_xml{end+1} = sprintf('  <aircraft name="%s" ac_id="%d">', current_ac_name, current_ac_id);
    all_aircraft_xml{end+1} = '    <settings>';
    for i = 1:length(current_settings_xml)
        all_aircraft_xml{end+1} = ['      ' current_settings_xml{i}];
    end
    all_aircraft_xml{end+1} = '    </settings>';
    all_aircraft_xml{end+1} = '  </aircraft>';
end

% Write single XML file with all aircraft
if gen_xml && ~isempty(all_aircraft_xml)
    xml_filename = fullfile(filepath, sprintf('%s_settings.xml', log_base_name));
    fxml = fopen(xml_filename, 'w');
    fprintf(fxml, '<?xml version="1.0" encoding="UTF-8"?>\n');
    fprintf(fxml, '<settings>\n');
    fprintf(fxml, '  <!-- Settings for all aircraft in log file -->\n');
    
    for i = 1:length(all_aircraft_xml)
        fprintf(fxml, '%s\n', all_aircraft_xml{i});
    end
    
    fprintf(fxml, '</settings>\n');
    fclose(fxml);
end

fclose(flog);

% Filter the output struct to only include aircraft that were actually present in data
if ~isempty(s.aircrafts)
    present_indices = [];
    for i = 1:length(s.aircrafts)
        if isKey(present_ac_ids, uint32(s.aircrafts(i).AC_ID))
            present_indices = [present_indices, i];
        end
    end
    if ~isempty(present_indices)
        s.aircrafts = s.aircrafts(present_indices);
    else
        s.aircrafts = struct();
    end
end

end
