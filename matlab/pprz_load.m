function logs = pprz_load(varargin)
%PPRZ_LOAD Load MATLAB exports of the flight logbook (format 4).
%   s = pprz_load('x.mat')               one export
%   logs = pprz_load('folder')           every .mat in a folder and its subfolders
%   logs = pprz_load('selection.zip')    every .mat in a bulk download
%   logs = pprz_load('a.mat', 'b.mat')   several files
%   logs = pprz_load()                   pick files in a dialog
%
%   Returns a struct array of the exported s structs (see README.md), each with the field 'file' added.
%   Convert a local .data/.log first with: python3 webui/matlab_export.py LOG.data [--flights]
    if nargin == 0
        [names, folder] = uigetfile({'*.mat;*.zip', 'Logbook MATLAB exports (*.mat, *.zip)'}, ...
            'Select exports', 'MultiSelect', 'on');
        if isequal(names, 0)
            logs = struct([]);
            return
        end
        varargin = fullfile(folder, cellstr(names));
    end
    files = {};
    for k = 1:numel(varargin)
        p = char(varargin{k});
        if isfolder(p)
            list = dir(fullfile(p, '**', '*.mat'));
            files = [files, fullfile({list.folder}, {list.name})]; %#ok<AGROW>
        elseif endsWith(lower(p), '.zip')
            extracted = unzip(p, tempname);
            files = [files, extracted(endsWith(lower(extracted), '.mat'))]; %#ok<AGROW>
        else
            files{end + 1} = p; %#ok<AGROW>
        end
    end
    if isempty(files)
        error('pprz_load:empty', 'No .mat files found.');
    end
    loaded = cell(1, numel(files));
    for k = 1:numel(files)
        x = load(files{k}, 's');
        if ~isfield(x, 's') || ~isfield(x.s, 'info') || ~strcmp(x.s.info.exporter_version, '4')
            error('pprz_load:format', ['%s is not a format 4 export. Export it again from the logbook ' ...
                  '(or with webui/matlab_export.py).'], files{k});
        end
        s = x.s;
        s.file = files{k};
        loaded{k} = orderfields(s);
    end
    logs = [loaded{:}];
end
