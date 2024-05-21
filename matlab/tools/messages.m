function s = messages(filename)
% Parse the messages XML file

% General variables
paparazzi_home = getenv('PAPARAZZI_HOME');

% When no filename is given
if nargin < 1 || isempty(filename)
    % First use PAPARAZZI_HOME else use messages xml from current folder
    if isempty(paparazzi_home)
        paparazzi_var = pwd;
    else
        paparazzi_var = fullfile(paparazzi_home, 'var');
    end
    
    % Set the filename
    filename = fullfile(paparazzi_var, 'messages.xml');
end

% Check if the file exists
if exist(filename, 'file') ~= 2
    error("Could not find messages.xml file at '%s' (PAPARAZZI_HOME='%s')", filename, paparazzi_home)
end

% Try to parse the messages xml file
builder = javax.xml.parsers.DocumentBuilderFactory.newInstance;
builder.setFeature('http://apache.org/xml/features/nonvalidating/load-external-dtd', false);
root_node = xmlread(filename, builder);
msg_classes = root_node.getElementsByTagName('msg_class');

% Go throught the message classes
for i = 1:msg_classes.getLength
    msg_class = parse_msg_class(msg_classes.item(i-1));

    % If no valid name was found continue else append
    %if isempty(msg_class.name)
    %    continue
    %else
        s.(msg_class.name) = msg_class.msgs;
    %end
end
end

% Parse a msg_class node from the protocol
function c = parse_msg_class(node)
    % Try to parse the message class name
    c.name = string(node.getAttribute('name'));
    if c.name == ""
        c.name = string(node.getAttribute('NAME'));
    end
    
    % If no valid name was found continue
    if c.name == ""
        warning("Messages xml contains an invalid msg_class without name")
        return
    end
    
    % Go through the messages and parse them
    msgs_nodes = node.getElementsByTagName('message');
    for i = 1:msgs_nodes.getLength
        msg = parse_message(msgs_nodes.item(i-1));
        c.msgs.(msg.name) = msg;
    end
end

% Parse a message node from the msg_class
function c = parse_message(node)
    % Try to parse the message name
    c.name = string(node.getAttribute('name'));
    if c.name == ""
        c.name = string(node.getAttribute('NAME'));
    end
    
    % If no valid name was found continue
    if c.name == ""
        warning("Messages xml contains an invalid message without name")
        return
    end
    
    % Go through the fields (and optional descriptions)
    c.field_names = [];
    c.field_parser = [];
    c.field_isarray = [];
    field_nodes = node.getElementsByTagName('field');
    for i = 1:field_nodes.getLength
        field = parse_field(field_nodes.item(i-1));
        c.field_parser = [c.field_parser, field2parser(field)];
        c.field_isarray = [c.field_isarray, fieldisarray(field)];
        c.field_names = [c.field_names, field.name];
        c.fields.(field.name) = field;
    end
end

% Parse a field node from a message
function c = parse_field(node)
    % Try to parse the field attributes    
    c.name = string(node.getAttribute('name'));
    if c.name == ""
        c.name = string(node.getAttribute('NAME'));
    end
    
    c.type = node.getAttribute('type');
    if c.type.equals("")
        c.type = node.getAttribute('TYPE');
    end
    
    c.unit = node.getAttribute('unit');
    if c.unit.equals("")
        c.unit = node.getAttribute('UNIT');
    end
    
    c.values = node.getAttribute('values');
    if c.values.equals("")
        c.values = node.getAttribute('VALUES');
    end
    
    c.alt_unit = node.getAttribute('alt_unit');
    if c.alt_unit.equals("")
        c.alt_unit = node.getAttribute('ALT_UNIT');
    end
    
    c.alt_unit_coef = node.getAttribute('alt_unit_coef');
    if c.alt_unit_coef.equals("")
        c.alt_unit_coef = node.getAttribute('ALT_UNIT_COEF');
    end
    
    if c.alt_unit_coef.equals("")
        c.alt_unit_coef = 1;
    else
        c.alt_unit_coef = str2double(c.alt_unit_coef);
    end
end

function c = field2parser(field)
    c = '';
    field_type = lower(string(field.type));
    if field_type == "uint8" || field_type == "uint16" || field_type == "uint32"
    	c = '%u';
    elseif field_type == "int8" || field_type == "int16" || field_type == "int32"
    	c = '%d';
    elseif field_type == "float" || field_type == "double"
        c = '%f';
    elseif field_type == "char"
        c = '%c';
    elseif field_type == "char[]" || field_type == "string"
        c = '%q'; % Quoted text
    elseif regexp(field_type, "[a-z0-9]+\[[0-9]*\]")
        c = '%s';
    else
        error("Could not parse field type '%s'", field_type)
    end
    c = string(c);
end

function c = fieldisarray(field)
    c = false;
    field_type = lower(string(field.type));
    if field_type ~= "char[]" && field_type ~= "string" && ~isempty(regexp(field_type, "[a-z0-9]+\[[0-9]*\]"))
        c = true;
    end
end
