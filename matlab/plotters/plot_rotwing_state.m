function plot_rotwing_state(ac_data, vert)
    if ~isfield(ac_data, 'ROTATING_WING_STATE')
        return
    end
    
    rng(0);

    % Plot the state
    ax1 = subplot(4,1,1);
    skew_angle_valid = bitget(ac_data.ROTATING_WING_STATE.status, 1);
    hover_motors_enabled = bitget(ac_data.ROTATING_WING_STATE.status, 2);
    hover_motors_idle = bitget(ac_data.ROTATING_WING_STATE.status, 3);
    hover_motors_running = bitget(ac_data.ROTATING_WING_STATE.status, 4);
    pusher_motor_running = bitget(ac_data.ROTATING_WING_STATE.status, 5);
    skew_forced = bitget(ac_data.ROTATING_WING_STATE.status, 6);

    plot(ac_data.ROTATING_WING_STATE.timestamp, [ac_data.ROTATING_WING_STATE.state, ac_data.ROTATING_WING_STATE.nav_state], '-')
    hold on
    if exist('vert','var')
        hold on;
        plot([vert'; vert'], repmat(ylim',1,size(vert,1)), '--r');
    end
    title('State');
    xlabel('Time [s]');
    legend('State', 'NAV State');
    
    % Plot bitmask bars
    ax2 = subplot(4,1,2);
    bit_mask_arr = int32([skew_angle_valid, hover_motors_enabled, hover_motors_idle, hover_motors_running, pusher_motor_running, skew_forced]);
    ticks = {'Skew valid'; 'Hover motors enabled'; 'hover motors idle'; 'hover motors running'; 'pusher motor running'; 'force skew'};
    ylim([0, size(bit_mask_arr, 2)])
    set(gca, 'YTick', 0.5:1:0.5+size(bit_mask_arr, 2), 'YTickLabel', ticks);
    xlabel("Time [s]")
    hold on;
    
    for i = 1:size(bit_mask_arr, 2)
        color = rand(1,3);
        bit_flip_idx = find((diff(bit_mask_arr(:, i)) ~= 0) == 1);
        if ~isempty(bit_flip_idx)
            for j = 1:numel(bit_flip_idx) + 1
                if j == 1
                    height = double(bit_mask_arr(1, i));
                    height(height < 0) = 0;
                    rectangle('Position', [ac_data.ROTATING_WING_STATE.timestamp(1), i-1, ac_data.ROTATING_WING_STATE.timestamp(bit_flip_idx(j)+1) - ac_data.ROTATING_WING_STATE.timestamp(1), height], ...
                        'FaceColor', color, 'EdgeColor', [0 0 0], 'FaceAlpha', 0.3);
                elseif j <= numel(bit_flip_idx)
                    height = double(bit_mask_arr(bit_flip_idx(j), i));
                    height(height < 0) = 0;
                    width = ac_data.ROTATING_WING_STATE.timestamp(bit_flip_idx(j)+1) - ac_data.ROTATING_WING_STATE.timestamp(bit_flip_idx(j-1)+1);
                    rectangle('Position', [ac_data.ROTATING_WING_STATE.timestamp(bit_flip_idx(j-1)+1), i-1, width, height], ...
                        'FaceColor', color, 'EdgeColor', [0 0 0], 'FaceAlpha', 0.3);
                else
                    height = double(bit_mask_arr(end, i));
                    height(height < 0) = 0;
                    rectangle('Position', [ac_data.ROTATING_WING_STATE.timestamp(bit_flip_idx(end)+1), i-1, ac_data.ROTATING_WING_STATE.timestamp(end) - ac_data.ROTATING_WING_STATE.timestamp(bit_flip_idx(end)+1), height], ...
                        'FaceColor', color, 'EdgeColor', [0 0 0], 'FaceAlpha', 0.3);
                end
            end
        else
            height = double(bit_mask_arr(1, i));
            height(height < 0) = 0;
            rectangle('Position', [ac_data.ROTATING_WING_STATE.timestamp(1), i-1, ac_data.ROTATING_WING_STATE.timestamp(end) - ac_data.ROTATING_WING_STATE.timestamp(1), height], ...
                'FaceColor', color, 'EdgeColor', [0 0 0], 'FaceAlpha', 0.3);
        end
    end
    
    % To have datapoints to click for reference
    plot(ac_data.ROTATING_WING_STATE.timestamp, ones(size(bit_mask_arr)).*repmat(1:size(bit_mask_arr, 2), numel(ac_data.ROTATING_WING_STATE.timestamp), 1), "Color", "black");
    if exist('vert','var')
        hold on;
        plot([vert'; vert'], repmat(ylim',1,size(vert,1)), '--r');
    end
    title('Bit flags')
    
    % Plot skew angle
    ax3 = subplot(4,1,3);
    plot(ac_data.ROTATING_WING_STATE.timestamp, [ac_data.ROTATING_WING_STATE.meas_skew_angle, ac_data.ROTATING_WING_STATE.sp_skew_angle]);
    if exist('vert','var')
        hold on;
        plot([vert'; vert'], repmat(ylim',1,size(vert,1)), '--r');
    end
    title('Skew');
    xlabel('Time [s]');
    legend('Measured [deg]', 'Setpoint [deg]');
    
    % Plot airspeed
    ax4 = subplot(4,1,4);
    resamp_airspeed = resample(timeseries(ac_data.AIR_DATA.airspeed, ac_data.AIR_DATA.timestamp), ac_data.ROTATING_WING_STATE.timestamp);
    plot(ac_data.ROTATING_WING_STATE.timestamp, [resamp_airspeed.Data, ac_data.ROTATING_WING_STATE.nav_airspeed, ac_data.ROTATING_WING_STATE.min_airspeed,  ac_data.ROTATING_WING_STATE.max_airspeed]);
    if exist('vert','var')
        hold on;
        plot([vert'; vert'], repmat(ylim',1,size(vert,1)), '--r');
    end
    title('Airspeed');
    xlabel('Time [s]');
    legend('Meas airspeed [m/s]', 'NAV Airspeed [m/s]', 'Min Airspeed [m/s]', 'Max Airspeed [m/s]');

    sgtitle('Rotwing state')
    linkaxes([ax1,ax2,ax3],'x')

end