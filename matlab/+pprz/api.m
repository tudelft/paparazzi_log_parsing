function out = api(method, path, varargin)
%API Call the logbook API with the server and key of pprz_server. method is 'GET' or 'POST'; a POST sends the
%   name/value pairs in varargin as form fields (MATLAB and Octave both support those).
%   Errors carry the server's message, e.g. an unknown flight or a key without access.
    cfg = pprz_server();
    if isempty(cfg.server) || isempty(cfg.key)
        error('pprz:server', 'Set the logbook first: pprz_server(''https://logs.example.org'', ''API-KEY'')');
    end
    opts = weboptions('HeaderFields', {'Authorization', ['Bearer ' cfg.key]}, 'Timeout', 60, ...
        'ContentType', 'json');
    url = [cfg.server path];
    try
        if strcmpi(method, 'POST')
            out = webwrite(url, varargin{:}, opts);
        else
            out = webread(url, opts);
        end
    catch err
        error('pprz:api', '%s %s failed: %s', method, path, err.message);
    end
    if ischar(out) && ~isempty(out)  % Octave returns the JSON text
        out = jsondecode(out);
    end
end
