function cfg = pprz_server(varargin)
%PPRZ_SERVER Logbook server, API key and cache folder used by pprz_fetch and pprz_logbook.
%   pprz_server('https://logs.example.org', 'KEY')   store the server and an API key (My account > API keys;
%                                                      a read-only key is enough)
%   pprz_server('cache', folder)                      where fetched .mat files are kept
%   cfg = pprz_server()                               the current settings (the key is masked)
%   pprz_server('clear')                              forget the server and key
%   Settings are MATLAB preferences of this user (group 'pprz_logbook'), kept between sessions. The key is
%   stored in plain text in the preferences folder, like other MATLAB preferences.
    group = 'pprz_logbook';
    if nargin == 1 && strcmpi(varargin{1}, 'clear')
        for name = {'server', 'key'}
            if ispref(group, name{1})
                rmpref(group, name{1});
            end
        end
    elseif nargin == 2 && strcmpi(varargin{1}, 'cache')
        setpref(group, 'cache', char(varargin{2}));
    elseif nargin >= 1
        url = regexprep(char(varargin{1}), '/+$', '');
        if isempty(regexp(url, '^https?://', 'once'))
            error('pprz_server:url', 'The server must start with https:// (or http://), e.g. https://logs.example.org');
        end
        setpref(group, 'server', url);
        if nargin >= 2
            setpref(group, 'key', char(varargin{2}));
        end
    end
    cfg = struct('server', pref(group, 'server', ''), 'key', pref(group, 'key', ''), ...
        'cache', pref(group, 'cache', fullfile(prefdir, 'pprz_logbook_cache')));
    if nargout == 0 && nargin == 0
        key = cfg.key;
        if numel(key) > 8
            key = [key(1:6) '…'];
        end
        fprintf('Server: %s\nAPI key: %s\nCache: %s\n', blank(cfg.server), blank(key), cfg.cache);
    end
    if nargout == 0
        clear cfg
    end
end

function v = pref(group, name, default)
    if ispref(group, name)
        v = getpref(group, name);
    else
        v = default;
    end
end

function s = blank(s)
    if isempty(s)
        s = '(not set)';
    end
end
