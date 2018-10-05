function s = parselog(filename, msgs)

% Try to find the correct messages xml file
if nargin < 2
    msg_filename = splitlog(filename);
    if size(msg_filename) >= 0
        msgs = messages(msg_filename);
    else
        msgs = messages();
    end
end
s.msgs = msgs;

% Open the data file
[filepath, name,] = fileparts(filename);
fid = fopen(strcat(filepath, filesep, name, '.data'));

% Read everything as timestamp, A/C ID, msg name and msg contents
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

% Go thorugh all the AC in the log
for iAC = nAC:-1:1 % counting backwards eliminates preallocation
    ac_id = uniqueAC(iAC);
    msg_ids = (aircraftID == ac_id);
    
    % Set the AC_ID and parse the data from the log for that AC
    s.aircafts(iAC).AC_ID = ac_id;
    s.aircafts(iAC).data = parse_aircraft_data(msgs, uniqueMsg, timestamp(msg_ids), msgName(msg_ids), msgContent(msg_ids));
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
        msg_heads = msgs.telemetry.(msg_name).field_names;
        msg_ids = strcmp(msg_name, msgName);

        % Set the timestamp and parse the content from the XML heads
        s.(msg_name).timestamp = timestamp(msg_ids);
        content = split(string(msgContent(msg_ids)));
        nContent = size(msg_heads, 2);
        
        for j = nContent:-1:1
            msg_head = msg_heads(j);
            s.(msg_name).(msg_head) = str2double(content(:, j));
        end
    end
end

% Split the .log file in seperate files which can be parsed
function s = splitlog(filename)
    [filepath, name,] = fileparts(filename);
    log_filename = strcat(filepath, filesep, name, '.log');
    msgs_filename = strcat(filepath, filesep, name, '_msgs.xml');
    
    flog = fopen(log_filename, 'rt');
    fmsgs = fopen(msgs_filename, 'w');
    got_prot = false;
    s = '';
    
    while 1
         tline = fgetl(flog);
         
         % Check for the start
         if contains(tline, '<protocol>')
             got_prot = true;
         end
         
         % Check if we need to output the messages xml
         if got_prot
             fprintf(fmsgs, "%s\n", tline);
         end
         
         % When it is the end of the file or protocol stop
         if ~ischar(tline) || contains(tline, '</protocol>')
             break
         end
    end
    
    fclose(flog);
    fclose(fmsgs);
    
    % Return the filename if we split of a messages files
    if got_prot
        s = msgs_filename;
    end
end