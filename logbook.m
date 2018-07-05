    clear;
close all;
path(path,'./matlab/');

% Logbook

log1 = struct('type', 'test', 'file', '18_06_20__10_47_22.data', 'good', [1464 3639], 'flight', [1555 3572], 'comment', 'DC3 efficiency test forward EHVB 2018 - 6 - 20')

log2 = struct('type', 'test', 'file', '18_07_03__15_53_26.data', 'good', [1100 3453], 'flight', [1232 3493], 'comment', '4 laps in manual forward')
log3 = struct('type', 'test', 'file', '18_07_03__17_39_37.data', 'good', [1500 3800 ], 'flight', [1614 3757], 'comment', 'Making hours, battery testing')

log4 = struct('type', 'test', 'file', '18_07_03__19_35_46.data', 'good', [191 2648], 'flight', [310 2555], 'comment', 'First auto take-off of 3MM')
log5 = struct('type', 'test', 'file', '18_07_03__21_41_07.data', 'good', [172 3167 ], 'flight', [201.2 3078], 'comment', 'Longest testflight of any DC')




logs = {log1, log2, log3, log4, log5};
%logs = {log4};

tot_fl = 0;
tot_sec = 0;

for i=1:max(size(logs))
    log = cell2mat(logs(i));
    close all;
    [r,gps,temp, mot, fbw, energy, status, air] = read_rotorcraft_log(log.file);

    log.r=r;
    log.gps=gps;
    log.temp=temp;
    log.mot=mot;
    log.fbw=fbw;
    log.energy=energy;
    log.status=status;
    log.air=air;

    plot_rotor(log,i);
    if (size(log.flight,2) == 2)
        fl = log.flight(:,2) - log.flight(:,1);
        for j=1:size(log.flight,1);
            %if (strcmp(log.type,'d2'))
                disp(i)
                tot_fl = tot_fl + 1;
                tot_sec = tot_sec + fl(j);
            %end
        end
    end
end

tot_fl
tot_sec

%%
