%% PPRZ_EXAMPLE  Example session: load a flight, make plots, make your own plots, analyse.
% Run one section at a time (Ctrl+Enter, or "Run Section"), change the file names and plot names, and copy
% what you need into your own scripts. Everything after the loading section also works offline.
% See matlab/README.md for the full list of functions and plots.

%% 0. Paths (once per MATLAB session; run from the repository root or the matlab folder)
here = fileparts(which('pprz_example'));
addpath(genpath(here))                    % pprz_*.m, plotters/, +pprz/ and 3d_animation/
close all

%% 1a. Load a local Paparazzi log (.data with its .log next to it), also in the field without internet
% Once per computer, with internet: pprz_setup('install', true)
logs = pprz_load('24_10_30__16_27_37_SD.data');     % converted once to ..._whole_log.mat next to it

%% 1b. ...or flights from the logbook server by their number (/flight/<number>), cached locally
% Once: pprz_server('https://logs.example.org', 'API-KEY')   (My account > API keys; read-only is enough)
% T = pprz_logbook();                                % table of the flights you may see
% logs = pprz_fetch([1234 1240]);

%% 1c. ...or exports downloaded from the logbook (.mat, a bulk .zip, or a folder of them)
% logs = pprz_load('selection.zip');
% logs = pprz_load();                                % pick files in a dialog

%% 2. Pick a flight
T = pprz_flights(logs)                               % one row per flight; flight 0 is the whole log
f = pprz_flight(logs, find(T.flight > 0, 1));        % the first detected flight (or use a row number)
disp(f.title)
disp(f.summary)                                      % duration, distance, takeoff/landing position, verdict
ev = pprz.events(f);                                 % mode changes, setting changes, checklist, INFO_MSG
disp(struct2table(ev))

%% 3. Interactive viewer: pick plots, time window, linked cursor, 3-D replay, system ID, comparisons
pprz_view(logs)

%% 4. Ready-made plots by name (pprz_plot() lists them all)
pprz_plot()                                          % the list with the messages each plot needs
pprz_plot(f, 'overview', 'euler', 'actuators')
pprz_plot(f, 'position', 'modes', 'ap')              % shade the autopilot modes
pprz_plot(f, 'imu', 'range', [0 60])                 % only the first minute after takeoff
pprz_plot(f, 'cmd', 'events', {'mode', 'message'})   % mode changes and INFO_MSG as markers
% figs = pprz_plot(f, 'docked', true);               % every logged plot as tabs in one window

%% 5. Individual plotters (plotters/plot_*.m), like the old debug_flight.m: each draws into the current figure
d = f.data;
if pprz.has(d, 'ROTORCRAFT_STATUS'), figure('Name', 'Rotorcraft status'); plot_rotorcraft_status(d, f); end
if pprz.has(d, 'ROTORCRAFT_FP'),     figure('Name', 'Rotorcraft FP');     plot_rotorcraft_fp(d); end
if pprz.has(d, 'GPS_INT'),           figure('Name', 'GPS');               plot_gps(d); end
if pprz.has(d, 'ACTUATORS'),         figure('Name', 'Actuators 1-4');     plot_actuators(d, 1:4); end
if pprz.has(d, 'AIR_DATA'),          figure('Name', 'Airspeed');          plot_airspeed(d); end

%% 6. Your own plot: any message field against its time column t (seconds from takeoff)
fieldnames(d)                                        % the logged messages
fieldnames(d.ROTORCRAFT_FP)                          % the fields of one message
figure('Name', 'Height and climb');
ax = subplot(2, 1, 1);
plot(d.ROTORCRAFT_FP.t, d.ROTORCRAFT_FP.up); grid on
ylabel(['Up [' pprz.unit(f, 'ROTORCRAFT_FP', 'up') ']'])
ax(2) = subplot(2, 1, 2);
plot(d.ROTORCRAFT_FP.t, d.ROTORCRAFT_FP.vup); grid on
ylabel(['Climb [' pprz.unit(f, 'ROTORCRAFT_FP', 'vup') ']']); xlabel('Time [s]')
linkaxes(ax, 'x')
pprz.modes(f, gcf, 'ap')                             % shade the autopilot modes
pprz.markers(f, gcf, {'mode', 'setting'})            % mode and setting changes as labelled lines

%% 7. Helpers for your own analysis
[u, names, tu] = pprz.actuators(d);                  % ACTUATORS as an N×k matrix with names
[tr, pqr, src] = pprz.rates(d);                      % body rates p, q, r [rad/s] from the logged IMU messages
dc = pprz.crop(d, 10, 40);                           % every message between 10 and 40 s
mode = pprz.enum(f, 'ROTORCRAFT_STATUS', 'ap_mode', d.ROTORCRAFT_STATUS.ap_mode);   % mode names
fs = 1 / median(diff(tr));
p_filtered = pprz.lowpass(pqr(:, 1), 5, fs);         % zero-phase low-pass at 5 Hz
figure('Name', 'Roll rate'); pprz.series(gca, tr, [pqr(:, 1), p_filtered], {'p', 'p low-pass 5 Hz'})
xlabel('Time [s]'); ylabel('p [rad/s]')

%% 8. Linked cursor over several figures (click or drag in a time plot; arrow keys step)
c = pprz.Cursor(findall(groot, 'Type', 'figure'));
c.move(5);                                           % 5 s after takeoff
disp(struct2table(c.values()))                       % every plotted value at the cursor

%% 9. Analysis tools
pprz_replay(f);                                      % 3-D replay with the airframe model
ex = pprz_sysid(f);                                  % doublets, chirps, setting and RC steps
r = pprz_effectiveness(f, 'range', [10 60]);         % control effectiveness per actuator
% pprz_compare({logs, [2 3]}, 'euler', 'ROTORCRAFT_FP.up')   % overlay rows 2 and 3 of pprz_flights(logs)

%% 10. Save figures and data
% exportgraphics(gcf, 'height.png', 'Resolution', 200)
% writetable(struct2table(d.ROTORCRAFT_FP), 'rotorcraft_fp.csv')
