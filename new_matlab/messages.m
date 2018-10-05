function s = messages(filename)
% Parse the messages XML file

% General variables
paparazzi_home = getenv('PAPARAZZI_HOME');

% When no filename is given
if nargin < 1
    % First use PAPARAZZI_HOME else use messages xml from current folder
    if isempty(paparazzi_home) == 0
        paparazzi_var = pwd;
    else
        paparazzi_var = fullfile(paparazzi_home, 'var');
    end
    
    % Set the filename
    filename = fullfile(paparazzi_var, 'messages.xml');
end

% Try to parse the messages xml file
builder = javax.xml.parsers.DocumentBuilderFactory.newInstance;
builder.setFeature('http://apache.org/xml/features/nonvalidating/load-external-dtd', false);
%try
    root_node = xmlread(filename, builder);
    protocols = root_node.getChildNodes.item(0).getChildNodes;
    
    % Go throught the protocols
    for i = 1:protocols.getLength
        prot_node = protocols.item(i-1);
        
        % Check if it is a message class
        if lower(string(prot_node.getNodeName)) == "msg_class"
            msg_class = parse_msg_class(prot_node);
            
            % If no valid name was found continue else append
            if isempty(msg_class.name)
                continue
            else
                s.(msg_class.name) = msg_class.msgs;
            end
        end
    end
%catch
%    error('Messages xml not found at %s (PAPARAZZI_HOME=%s)', filename, paparazzi_home)
%end
end

% Parse a msg_class node from the protocol
function c = parse_msg_class(node)
    % Try to parse the message class name
    prot_attribs = node.getAttributes;
    c.name = '';

    for j = 1:prot_attribs.getLength
        if lower(string(prot_attribs.item(j-1).getName)) == "name"
            c.name = string(prot_attribs.item(j-1).getValue);
        end
    end
    
    % If no valid name was found continue
    if size(c.name) <= 0
        warning("Messages xml contains an invalid msg_class without name")
        return
    end
    
    % Go through the messages
    msgs_nodes = node.getChildNodes;
    for i = 1:msgs_nodes.getLength
        msg_node = msgs_nodes.item(i-1);
        if lower(string(msg_node.getNodeName)) == "message"
            msg = parse_message(msg_node);
            c.msgs.(msg.name) = msg;
        end
    end
end

% Parse a message node from the msg_class
function c = parse_message(node)
    % Try to parse the message name
    msg_attribs = node.getAttributes;
    c.name = '';

    for j = 1:msg_attribs.getLength
        if lower(string(msg_attribs.item(j-1).getName)) == "name"
            c.name = string(msg_attribs.item(j-1).getValue);
        end
    end
    
    % If no valid name was found continue
    if isempty(c.name)
        warning("Messages xml contains an invalid message without name")
        return
    end
    
    % Go through the fields (and optional descriptions)
    field_names = [];
    field_nodes = node.getChildNodes;
    for i = 1:field_nodes.getLength
        field_node = field_nodes.item(i-1);
        if lower(string(field_node.getNodeName)) == "field"
            field = parse_field(field_node);
            field_names = [field_names, field.name];
            c.fields.(field.name) = field;
        end
    end
    c.field_names = field_names;
end

% Parse a field node from a message
function c = parse_field(node)
    % Try to parse the field attributes
    field_attribs = node.getAttributes;
    c.name = '';
    c.type = '';
    c.unit = '';
    c.values = '';

    for j = 1:field_attribs.getLength
        if lower(string(field_attribs.item(j-1).getName)) == "name"
            c.name = string(field_attribs.item(j-1).getValue);
        elseif lower(string(field_attribs.item(j-1).getName)) == "type"
            c.type = field_attribs.item(j-1).getValue;
        elseif lower(string(field_attribs.item(j-1).getName)) == "unit"
            c.unit = field_attribs.item(j-1).getValue;
        elseif lower(string(field_attribs.item(j-1).getName)) == "values"
            c.values = field_attribs.item(j-1).getValue;
        end
    end
end
