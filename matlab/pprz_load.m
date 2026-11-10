function logs = pprz_load(varargin)
%PPRZ_LOAD Load MATLAB exports of the flight logbook (format 5).
%   s = pprz_load('x.mat')               one export
%   logs = pprz_load('folder')           every .mat in a folder and its subfolders
%   logs = pprz_load('selection.zip')    every .mat in a bulk download
%   logs = pprz_load('a.mat', 'b.mat')   several files
%   s = pprz_load('24_10_30__16_27_37_SD.data')   a Paparazzi log (.data or .data.gz, or its .log), converted
%                                        offline to a whole-log export next to it and reused afterwards
%   logs = pprz_load()                   pick files in a dialog
%
%   A folder also converts the .data files in it. Converting needs pprz_setup once (Python with h5py and
%   the parser); after that it works without internet.
%   Returns a struct array of the exported s structs (see README.md), each with the field 'file' added.
%   Flights from the logbook server: pprz_fetch (after pprz_connect).
    if nargin == 0
        [names, folder] = uigetfile({'*.mat;*.zip;*.data;*.data.gz;*.log', ...
            'Exports and Paparazzi logs (*.mat, *.zip, *.data, *.log)'}, 'Select exports or logs', 'MultiSelect', 'on');
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
            logs_in = [dir(fullfile(p, '**', '*.data')); dir(fullfile(p, '**', '*.data.gz'))];
            for j = 1:numel(logs_in)
                files{end + 1} = pprz.convert(fullfile(logs_in(j).folder, logs_in(j).name)); %#ok<AGROW>
            end
            list = dir(fullfile(p, '**', '*.mat'));
            files = [files, fullfile({list.folder}, {list.name})]; %#ok<AGROW>
        elseif endsWith(lower(p), {'.data', '.data.gz'})
            files{end + 1} = pprz.convert(p); %#ok<AGROW>
        elseif endsWith(lower(p), '.log')
            data = [p(1:end - 4) '.data'];
            if ~isfile(data) && isfile([data '.gz'])
                data = [data '.gz'];
            end
            files{end + 1} = pprz.convert(data); %#ok<AGROW>
        elseif endsWith(lower(p), '.zip')
            extracted = unzip(p, tempname);
            files = [files, extracted(endsWith(lower(extracted), '.mat'))]; %#ok<AGROW>
        else
            files{end + 1} = p; %#ok<AGROW>
        end
    end
    files = unique(files, 'stable');  % a folder lists a converted .mat again
    if isempty(files)
        error('pprz_load:empty', 'No .mat or .data files found.');
    end
    loaded = cell(1, numel(files));
    for k = 1:numel(files)
        x = load(files{k}, 's');
        if ~isfield(x, 's') || ~isfield(x.s, 'info') || ~strcmp(x.s.info.exporter_version, '5')
            error('pprz_load:format', ['%s is not a format 5 export. Export it again from the logbook ' ...
                  '(or with webui/matlab_export.py).'], files{k});
        end
        s = x.s;
        s.file = files{k};
        loaded{k} = orderfields(s);
    end
    logs = [loaded{:}];
end
