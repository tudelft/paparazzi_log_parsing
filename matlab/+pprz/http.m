function [data, status] = http(method, url, body, key)
%HTTP One request without raising on HTTP errors: data is the decoded JSON (or text), status the HTTP status.
%   body: [] (none), a struct (sent as JSON) or a matlab.net.http content provider; key: API key or ''.
%   A failure to reach the server raises 'pprz:offline'.
    import matlab.net.http.*
    fields = HeaderField('Accept', 'application/json');
    if nargin >= 4 && ~isempty(key)
        fields(end + 1) = HeaderField('Authorization', ['Bearer ' char(key)]);
    end
    if nargin < 3 || isempty(body)
        req = RequestMessage(method, fields);
    elseif isstruct(body)
        fields(end + 1) = HeaderField('Content-Type', 'application/json');
        req = RequestMessage(method, fields, MessageBody(body));
    else
        req = RequestMessage(method, fields, body);
    end
    opts = HTTPOptions('ConnectTimeout', 30, 'ResponseTimeout', 900);
    try
        resp = req.send(matlab.net.URI(url), opts);
    catch err
        error('pprz:offline', 'Cannot reach %s: %s', url, err.message);
    end
    status = double(resp.StatusCode);
    data = resp.Body.Data;
    if (ischar(data) || isstring(data)) && ~isempty(data)
        try
            data = jsondecode(char(data));
        catch
            data = char(data);
        end
    end
end
