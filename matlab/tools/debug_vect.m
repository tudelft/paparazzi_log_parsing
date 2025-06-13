function [ac_data] = debug_vect(ac_data)

    if ~isfield(ac_data, 'DEBUG_VECT')
        return
    end

    rls_id = ac_data.DEBUG_VECT.name == "rls";
    names = unique(ac_data.DEBUG_VECT.name);

    N = length(names);

    for i = 1:N
        name = string(names{i});
        timestamp = ac_data.DEBUG_VECT.timestamp(ac_data.DEBUG_VECT.name == name);
        data = ac_data.DEBUG_VECT.vector(ac_data.DEBUG_VECT.name == name);

        
        num_commas = count(data{1}, ',');
        format_string = repmat('%f,', 1, num_commas+1);
        format_string(end) = []; % Remove the last comma

        M = cellfun(@(s) sscanf(s, format_string)', data, 'UniformOutput', false);
    
        data_mat = cell2mat(M);

        ac_data.DEBUG_VECT_PARSED.(name).timestamp = timestamp;
        ac_data.DEBUG_VECT_PARSED.(name).values = data_mat;
    end


% Plot the data
figure
    for i = 1:N
        ax = subplot(N,1,i);
        plot(ac_data.DEBUG_VECT_PARSED.(string(names{i})).timestamp, ac_data.DEBUG_VECT_PARSED.(string(names{i})).values)
        if exist('vert','var')
            hold on;
            plot([vert'; vert'], repmat(ylim',1,size(vert,1)), '--r');
        end
        title(string(names{i}));
        xlabel('Time [s]');

    end
    sgtitle('Debug vect messages')
    % linkaxes([ax1,ax2,ax3],'x')


end

