function out = api(method, path, body)
%API Call the logbook API with the connection of pprz_connect.
%   out = pprz.api('GET', '/api/me')
%   out = pprz.api('POST', path, struct(...))     JSON body
%   out = pprz.api('POST', path, provider)        a matlab.net.http content provider (uploads)
%   An expired or revoked browser login is renewed once (pprz.login). Errors 'pprz:http' carry the server's message,
%   e.g. an unknown flight or a key without access; 'pprz:offline' means the server could not be reached.
    if nargin < 3
        body = [];
    end
    c = pprz.connection();
    if isempty(c.key) && strcmp(c.source, 'browser login')
        c = relogin(c);
    end
    [out, status] = pprz.http(method, [c.server path], body, c.key);
    if status == 401 && strcmp(c.source, 'browser login')
        c = relogin(c);
        [out, status] = pprz.http(method, [c.server path], body, c.key);
    end
    if status >= 400
        message = '';
        if isstruct(out) && isfield(out, 'error')
            message = char(string(out.error));
        end
        if status == 401
            message = sprintf('%s (%s)', message, c.origin);
        end
        error('pprz:http', '%s %s failed: HTTP %d %s', method, path, status, message);
    end
end

function c = relogin(c)
    c.key = pprz.login(c.server);
    pprz.connection(c);
end
