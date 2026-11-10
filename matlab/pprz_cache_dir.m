function folder = pprz_cache_dir(folder)
%PPRZ_CACHE_DIR Folder where pprz_download and pprz_fetch keep downloads, shared with the Python package mavlab-logs.
%   folder = pprz_cache_dir()     the folder in use: MAVLAB_LOGS_CACHE, else the one set here, else the default
%                                 (see client/README.md, "Where files are kept")
%   pprz_cache_dir('D:\logs')     keep downloads there from now on ('' returns to the default)
    if nargin == 1
        cfg = pprz.config();
        cfg.cache = char(folder);
        pprz.config('save', cfg);
    end
    folder = pprz.config('cache');
    if nargout == 0
        fprintf('Cache: %s\n', folder);
        clear folder
    end
end
