    clear;
close all;
path(path,'./matlab/');

% Logbook

log1 = struct('type', 'D3', 'file', '18_06_20__10_47_22.data', 'good', [1464 3639], 'flight', [1555 3572], 'comment', 'DC3 efficiency test forward EHVB 2018 - 6 - 20')

log2 = struct('type', 'D3', 'file', '18_07_03__15_53_26.data', 'good', [1100 3453], 'flight', [1232 3493], 'comment', '4 laps in manual forward')
log3 = struct('type', 'D3', 'file', '18_07_03__17_39_37.data', 'good', [1500 3800 ], 'flight', [1614 3757], 'comment', 'Making hours, battery testing')

log4 = struct('type', 'D3', 'file', '18_07_03__19_35_46.data', 'good', [191 2648], 'flight', [310 2555], 'comment', 'First auto take-off of 3MM')
log5 = struct('type', 'D3', 'file', '18_07_03__21_41_07.data', 'good', [172 3167 ], 'flight', [201.2 3078], 'comment', 'Longest testflight of any DC')

log6 = struct('type', 'D3', 'file', '18_07_19__15_03_22.data', 'good', [304 2040], 'flight', [342 2002], 'comment', '26 blades. Auto TO and Land.')
log7 = struct('type', 'D3', 'file', '18_07_19__16_27_24.data', 'good', [1000 4300], 'flight', [1100 4255], 'comment', 'New endurance record. Auto TO and Land.')

log8 = struct('type', 'D3', 'file', '18_07_19__19_10_52.data', 'good', [110 4121], 'flight', [164 4118], 'comment', 'New endurance record. Auto TO and Land.')

log9 = struct('type', 'D3', 'file', '18_08_17__17_17_57.data', 'good', [ ], 'flight', [ ], 'comment', 'New efficient frame, speed record')


logbookentries = [];

logs = {log1, log2, log3, log4, log5, log6, log7, log8};
logs = {log9};

tot_fl = 0;
tot_sec = 0;

for i=1:max(size(logs))
    log = cell2mat(logs(i));
    close all;
    [r,gps,temp, mot, fbw, energy, status, air, curve] = read_rotorcraft_log(log.file);

    log.r=r;
    log.gps=gps;
    log.temp=temp;
    log.mot=mot;
    log.fbw=fbw;
    log.energy=energy;
    log.status=status;
    log.air=air;
    log.curve=curve;

    plot_rotor(log,i);
    fl = 0;
    if (size(log.flight,2) == 2)
        fl = log.flight(:,2) - log.flight(:,1);
        for j=1:size(log.flight,1);
            %if (strcmp(log.type,'d2'))
                disp(i)

                tot_fl = tot_fl + 1;
                tot_sec = tot_sec + fl(j);
                
                l. nr = tot_fl;
                l.name = log.file
                l.seconds = fl(j)
                
                logbookentries = [logbookentries; l];
                

            %end
        end
    end
    
    
end

tot_fl
tot_sec


%%

fprintf('\n----------------------------------------------\n')
fprintf('Logbook:\n')
tot_sec = 0;
for i=1:tot_fl
    sec = round(logbookentries(i).seconds);
    tot_sec = tot_sec + sec;
    min = floor(sec / 60);
    secr = sec - min*60;
    fprintf('Flight %d  % 3d:%02d min  %s \n', i,min, secr, logbookentries(i).name)
end
fprintf('----------------------------------------------\n')
sec = tot_sec;
hour = floor(tot_sec / 3600);
sec = tot_sec - hour * 3600;
min = floor(sec / 60);
secr = sec - min*60;
fprintf('Total: %d flights with % 4d:%02d:%02d hours \n\n', tot_fl, hour, min, secr)

