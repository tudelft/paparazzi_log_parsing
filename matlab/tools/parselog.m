function s = parselog(filename, msgs)

% Check if the data file exists
[filepath, name,] = fileparts(filename);
filename = strcat(filepath, filesep, name, '.data');

if exist(filename, 'file') ~= 2
    error("The log file does not exist '%s'", filename)
end

% Try to find the correct messages xml file
got_log = false;
if nargin < 2
    l = splitlog(filename);
    if ~isempty(l.msgs)
        msgs = messages(l.msgs);
        got_log = true;
    else
        warning('Parsing log file with only the .data file, which could lead to unexpected message fields.')
        msgs = messages();
    end
end
s.msgs = msgs;

% Read everything as timestamp, A/C ID, msg name and msg contents
fid = fopen(filename);
C = textscan(fid, '%f %u %s %[^\n]');
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
    s.aircrafts(iAC).data = parse_aircraft_data(msgs, uniqueMsg, timestamp(msg_ids), msgName(msg_ids), msgContent(msg_ids));
    
    % Additional info from the log file
    if got_log && isfield(l, "aircrafts")
        s.aircrafts(iAC).name = l.aircrafts(ac_id).name;
    end
    
    % Additional info from the data
    ac_data = s.aircrafts(iAC).data;
    if isfield(ac_data, "AUTOPILOT_VERSION")
        s.aircrafts(iAC).version = ac_data.AUTOPILOT_VERSION.version(1);
        s.aircrafts(iAC).version_desc = string(ac_data.AUTOPILOT_VERSION.desc(1));
    end
    if isfield(ac_data, "ROTORCRAFT_STATUS")
        s.aircrafts(iAC).in_flight = ac_data.ROTORCRAFT_STATUS.timestamp(find(diff(int32(ac_data.ROTORCRAFT_STATUS.ap_in_flight)))+1);
        s.aircrafts(iAC).motors_on = ac_data.ROTORCRAFT_STATUS.timestamp(find(diff(int32(ac_data.ROTORCRAFT_STATUS.ap_motors_on)))+1);
    end
end

% Close the log file
fclose(fid);
end

% Parse the log lines from a specific aircraft
function s = parse_aircraft_data(msgs, uniqueMsg, timestamp, msgName, msgContent)
    nMsg = size(uniqueMsg, 1);

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
        end
            
        % Get message fields and ids
        msg_fields = msg_info.field_names;
        msg_ids = strcmp(msg_name, msgName);

        % Set the timestamp and parse the content from the XML heads
        s.(msg_name).timestamp = timestamp(msg_ids);      
        nFields = size(msg_fields, 2);
        
        % Only parse content if needed
        if nFields > 0
            msgContentStr = strjoin(msgContent(msg_ids));
            field_parser = join(msg_info.field_parser, ' ');    
            content = textscan(msgContentStr, field_parser);
        end
        
        % Go through all the fields in the messages
        for j = 1:nFields
            field_name = msg_fields(j);
            field_isarray = msg_info.field_isarray(j);
            values = content{j};
            
            if field_isarray
                try
                    s.(msg_name).(field_name) = split(values, ",");%regexp(values, ',', 'split');
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
    end
end

% Split the .log file in seperate files which can be parsed
function s = splitlog(filename, gen_files)
    % Open the log file
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
    got_aircraft = false;
    
    % Go through the log file
    while 1
         tline = fgetl(flog);
         
         % When it is the end of the file stop
         if ~ischar(tline)
             break
         end
         
         % Check for the start
         if contains(tline, '<protocol')
             % Open a message file to write the protocol
             s.msgs = strcat(filepath, filesep, name, '_msgs.xml');
             fmsgs = fopen(s.msgs, 'w');
             got_prot = true;
         elseif contains(tline, '<aircraft')
             % Create a new aircraft
             aircraft.name = string(regexpi(tline, 'name="([^"]+)"', 'tokens'));
             aircraft.id = str2num(char(string(regexpi(tline, 'ac_id="([^"]+)"', 'tokens'))));
             aircraft.filename = strcat(filepath, filesep, name, '_ac', string(aircraft.id), '.xml');
             s.aircrafts(aircraft.id) = aircraft;
             
             % Open an aircraft file to write the aircraft output
             if gen_files
                 faircraft = fopen(aircraft.filename, 'w');
                 got_aircraft = true;
             end
         end
         
         % Check if we need to output lines
         if got_prot
             fprintf(fmsgs, "%s\n", tline);
         elseif got_aircraft
             fprintf(faircraft, "%s\n", tline);
         end
         
         % Check for the end
         if got_prot && contains(tline, '</protocol>')
             got_prot = false;
             fclose(fmsgs);
         elseif got_aircraft && contains(tline, '</aircraft>')
             got_aircraft = false;
             fclose(faircraft);
         end
    end
    
    % Close the log file
    fclose(flog);
end
