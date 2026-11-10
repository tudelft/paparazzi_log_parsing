function out = api(method, path, body)
%API Call the logbook API with the server and key of pprz_server, signing in through the browser when needed.
%   out = pprz.api('GET', '/api/me')
%   out = pprz.api('POST', path, struct(...))     JSON body
%   out = pprz.api('POST', path, provider)        a matlab.net.http content provider (uploads)
%   An expired or revoked browser login is renewed once (pprz_login). Errors 'pprz:http' carry the server's message,
%   e.g. an unknown flight or a key without access; 'pprz:offline' means the server could not be reached.
    if nargin < 3
        body = [];
    end
    cfg = pprz_server();
    if isempty(cfg.server)
        error('pprz:server', 'Set the logbook first: pprz_server(''https://logs.example.org'')');
    end
    if isempty(cfg.key)
        pprz_login(cfg.server);
        cfg = pprz_server();
    end
    [out, status] = pprz.http(method, [cfg.server path], body, cfg.key);
    if status == 401 && strcmp(cfg.source, 'stored')
        pprz_login(cfg.server);
        cfg = pprz_server();
        [out, status] = pprz.http(method, [cfg.server path], body, cfg.key);
    end
    if status >= 400
        message = '';
        if isstruct(out) && isfield(out, 'error')
            message = char(string(out.error));
        end
        error('pprz:http', '%s %s failed: HTTP %d %s', method, path, status, message);
    end
end
