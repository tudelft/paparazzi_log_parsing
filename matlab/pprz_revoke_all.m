function n = pprz_revoke_all(varargin)
%PPRZ_REVOKE_ALL Revoke your API keys from browser logins (all computers, MATLAB and Python) on the connected server.
%   pprz_revoke_all()                          the keys of browser logins; keys made on the account page keep working
%   pprz_revoke_all('include_manual', true)    all your API keys, also those made on the account page
%   n = pprz_revoke_all(...)                   the number of keys revoked
%   The browser login stored on this computer is forgotten; the next call that needs the server signs in again.
    opt = struct('include_manual', false);
    for k = 1:2:numel(varargin)
        opt.(lower(varargin{k})) = varargin{k + 1};
    end
    c = pprz.connection();
    out = pprz.api('POST', '/api/keys/revoke-all', struct('include_manual', logical(opt.include_manual)));
    n = out.revoked;
    stored = pprz.config('entry', c.server);
    if ~isempty(stored) && (opt.include_manual || ~isempty(stored.expires_at))
        pprz.config('forget', c.server);
    end
    if opt.include_manual || strcmp(c.source, 'browser login')
        c.key = '';
        c.source = 'browser login';
        pprz.connection(c);
    end
    fprintf('Revoked %d API key(s) on %s.\n', n, c.server);
    if nargout == 0
        clear n
    end
end
