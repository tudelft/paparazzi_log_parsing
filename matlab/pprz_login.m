function user = pprz_login(server)
%PPRZ_LOGIN Sign in to the logbook server through the browser; the key is stored for a week (pprz_server).
%   pprz_login()                              the server of pprz_server
%   pprz_login('https://logs.example.org')    another server, which becomes the default if none is set
%   Prints a link and a code and opens the browser: sign in there, check that it shows the same code and press
%   Allow. pprz_fetch, pprz_download, pprz_upload and pprz_logbook call it themselves when no valid key is stored.
%   The login is shared with the Python package mavlab-logs.
    if nargin < 1 || isempty(server)
        server = pprz_server().server;
        if isempty(server)
            error('pprz:server', 'Set the logbook first: pprz_server(''https://logs.example.org'')');
        end
    end
    server = regexprep(char(server), '/+$', '');
    [start, status] = pprz.http('POST', [server '/api/cli-login'], ...
        struct('client', ['MATLAB on ' char(java.net.InetAddress.getLocalHost().getHostName())]), '');
    if status ~= 200
        error('pprz:login', 'Cannot start the login (HTTP %d)', status);
    end
    fprintf('To sign in to %s, open\n  %s\nand check that it shows the code %s.\n', server, ...
        start.verification_uri_complete, start.user_code);
    try
        web(start.verification_uri_complete, '-browser');
    catch  % no browser here: the link works on any computer
    end
    interval = max(1, double(start.interval));
    deadline = datetime('now') + seconds(double(start.expires_in));
    while datetime('now') < deadline
        pause(interval);
        [token, status] = pprz.http('POST', [server '/api/cli-login/token'], struct('device_code', start.device_code), '');
        if status == 200
            pprz.config('store', server, token.access_token, token.expires_at, token.user.display_name);
            fprintf('Signed in as %s until %s.\n', token.user.display_name, token.expires_at);
            user = token.user;
            if nargout == 0
                clear user
            end
            return
        end
        code = '';
        if isstruct(token) && isfield(token, 'error')
            code = token.error;
        end
        if strcmp(code, 'slow_down')
            interval = interval + 5;
        elseif ~strcmp(code, 'authorization_pending')
            error('pprz:login', 'Login failed: %s', describe(token, status));
        end
    end
    error('pprz:login', 'Login timed out: the code was not confirmed in the browser in time');
end

function s = describe(token, status)
    s = sprintf('HTTP %d', status);
    if isstruct(token) && isfield(token, 'error_description')
        s = token.error_description;
    end
end
