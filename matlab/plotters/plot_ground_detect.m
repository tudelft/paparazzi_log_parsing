function plot_ground_detect(ac_data, vert)
    if ~isfield(ac_data, 'GROUND_DETECT')
        return
    end
    
    rng(0);
    
    figure('Name','Ground Detect Triggers');
    % Plot the state
    vspeed_trigger = bitget(ac_data.GROUND_DETECT.triggers, 1);
    spec_thrust_trigger = bitget(ac_data.GROUND_DETECT.triggers, 2);
    accel_filt_trigger = bitget(ac_data.GROUND_DETECT.triggers, 3);
    agl_trigger = bitget(ac_data.GROUND_DETECT.triggers, 4);
    hx711_trigger = bitget(ac_data.GROUND_DETECT.triggers, 5);
    ground_detected = ac_data.GROUND_DETECT.ground_detected;
    
    % Plot bitmask bars
    bit_mask_arr = int32([vspeed_trigger, spec_thrust_trigger, accel_filt_trigger, agl_trigger, hx711_trigger, ground_detected]);
    ticks = {'Vert speed trigger'; 'Spec Thrust trigger'; 'Vert Accel trigger'; 'AGL trigger'; 'HX711 trigger'; 'Ground Detected'};
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
                    rectangle('Position', [ac_data.GROUND_DETECT.ts(1), i-1, ac_data.GROUND_DETECT.ts(bit_flip_idx(j)+1) - ac_data.GROUND_DETECT.ts(1), height], ...
                        'FaceColor', color, 'EdgeColor', [0 0 0], 'FaceAlpha', 0.3);
                elseif j <= numel(bit_flip_idx)
                    height = double(bit_mask_arr(bit_flip_idx(j), i));
                    height(height < 0) = 0;
                    width = ac_data.GROUND_DETECT.ts(bit_flip_idx(j)+1) - ac_data.GROUND_DETECT.ts(bit_flip_idx(j-1)+1);
                    rectangle('Position', [ac_data.GROUND_DETECT.ts(bit_flip_idx(j-1)+1), i-1, width, height], ...
                        'FaceColor', color, 'EdgeColor', [0 0 0], 'FaceAlpha', 0.3);
                else
                    height = double(bit_mask_arr(end, i));
                    height(height < 0) = 0;
                    rectangle('Position', [ac_data.GROUND_DETECT.ts(bit_flip_idx(end)+1), i-1, ac_data.GROUND_DETECT.ts(end) - ac_data.GROUND_DETECT.ts(bit_flip_idx(end)+1), height], ...
                        'FaceColor', color, 'EdgeColor', [0 0 0], 'FaceAlpha', 0.3);
                end
            end
        else
            height = double(bit_mask_arr(1, i));
            height(height < 0) = 0;
            rectangle('Position', [ac_data.GROUND_DETECT.ts(1), i-1, ac_data.GROUND_DETECT.ts(end) - ac_data.GROUND_DETECT.ts(1), height], ...
                'FaceColor', color, 'EdgeColor', [0 0 0], 'FaceAlpha', 0.3);
        end
    end
    
    % To have datapoints to click for reference
    plot(ac_data.GROUND_DETECT.ts, ones(size(bit_mask_arr)).*repmat(1:size(bit_mask_arr, 2), numel(ac_data.GROUND_DETECT.ts), 1), "Color", "black");
    if exist('vert','var')
        hold on;
        plot([vert'; vert'], repmat(ylim',1,size(vert,1)), '--r');
    end
    title('Bit flags')
end