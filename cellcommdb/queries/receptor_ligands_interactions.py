import itertools
import pandas as pd

from cellcommdb.extensions import db
from cellcommdb.models import Gene, Multidata, Protein, ComplexComposition, Complex, Interaction


def call(cluster_counts, threshold=0.1):
    print('Receptor Ligands Interactions Initializated')
    clusters_names = cluster_counts.columns.values
    cluster_counts_cellphone = _cellphone_genes(cluster_counts)

    print('Aplicating Threshold')
    cluster_counts_filtered = _apply_threshold(cluster_counts_cellphone, clusters_names, threshold)

    print('Finding Complexes')
    cluster_counts_filtered['is_complex'] = False
    cluster_counts_with_complex = cluster_counts_filtered.append(
        _get_complex_involved(cluster_counts_filtered, clusters_names))

    print('Filtering Receptor / Ligands')
    _add_is_receptor_property(cluster_counts_with_complex)
    _add_is_ligand_property(cluster_counts_with_complex)

    print('Cluster Interactions')
    cluster_interactions = _get_all_cluster_interactions(clusters_names)

    interactions = _get_interactions()

    print('Finding Enabled Interactions')
    enabled_interactions = _get_enabled_interactions(cluster_counts_with_complex, interactions, 0.3)

    enabled_interactions.to_csv('out/TEST_enabled_interactions.csv', index=False)

    result_interactions = _result_interactions_table(cluster_interactions, enabled_interactions)
    result_interactions_extended = _result_interactions_extended_table(enabled_interactions, clusters_names,
                                                                       cluster_counts_filtered)

    return result_interactions, result_interactions_extended


def _result_interactions_extended_table(interactions, clusters_names, cluster_counts):
    result_receptor_complex = _get_counts_proteins_of_comlexes(cluster_counts, clusters_names, interactions,
                                                               '_receptors')

    result_receptor = interactions.loc[interactions['is_complex_receptors'] == False][
        ['interaction_id', 'entry_name_receptors', 'name_receptors', 'gene_name_receptors', 'is_complex_receptors'] + [
            cluster_name + '_receptors' for cluster_name in clusters_names]]

    def remove_suffix(column_name, suffix):
        if column_name.endswith(suffix):
            return column_name[:-len(suffix)]

        return column_name

    result_receptor.rename(columns=lambda column_name: remove_suffix(column_name, '_receptors'), inplace=True)

    result_receptor = result_receptor.append(result_receptor_complex)
    result_receptor = result_receptor.assign(receptor_ligand='receptor')

    result_ligand_complex = _get_counts_proteins_of_comlexes(cluster_counts, clusters_names, interactions, '_ligands')

    result_ligand = interactions.loc[interactions['is_complex_ligands'] == False][
        ['interaction_id', 'entry_name_ligands', 'name_ligands', 'gene_name_ligands', 'is_complex_ligands'] + [
            cluster_name + '_ligands' for cluster_name in clusters_names]]

    result_ligand.rename(columns=lambda column_name: remove_suffix(column_name, '_ligands'), inplace=True)
    result_ligand = result_ligand.append(result_ligand_complex)
    result_ligand = result_ligand.assign(receptor_ligand='ligand')

    result = result_receptor.append(result_ligand)
    result.sort_values('interaction_id').to_csv('out/TEST_result_interactions_extended.csv', index=False)

    return result


def _get_counts_proteins_of_comlexes(cluster_counts, clusters_names, interactions, suffix):
    receptor_complex_interactions = interactions.loc[interactions['is_complex%s' % suffix] == True]
    complex_composition_query = db.session.query(ComplexComposition.protein_multidata_id,
                                                 ComplexComposition.complex_multidata_id,
                                                 ComplexComposition.total_protein)
    complex_composition = pd.read_sql(complex_composition_query.statement, db.engine)
    receptor_complex_interactions = pd.merge(receptor_complex_interactions, complex_composition,
                                             left_on='multidata_id%s' % suffix, right_on='complex_multidata_id')
    receptor_complex_interactions = pd.merge(receptor_complex_interactions, cluster_counts,
                                             left_on='protein_multidata_id', right_on='multidata_id')
    receptor_complex_interactions.to_csv('out/TEST_receptor_complex_interactions.csv')
    result_receptor_complex = receptor_complex_interactions[
        ['interaction_id', 'entry_name', 'name', 'gene_name', 'name%s' % suffix] + list(clusters_names)]
    result_receptor_complex = result_receptor_complex.rename(columns={'name%s' % suffix: 'complex_name'}, index=str)
    result_receptor_complex = result_receptor_complex.assign(is_complex=True)
    result_receptor_complex.to_csv('out/TEST_receptor_complex.csv')
    return result_receptor_complex


def _result_interactions_table(cluster_interactions, enabled_interactions):
    result = enabled_interactions['interaction_id']
    cluster_interactions_columns_names = []
    for cluster_interaction in cluster_interactions:
        print(cluster_interaction)
        cluster_interaction_column_name = '%s - %s' % (cluster_interaction[0], cluster_interaction[1])
        cluster_interactions_columns_names.append(cluster_interaction_column_name)
        cluster_interaction_result = _check_receptor_ligand_interactions(cluster_interaction, enabled_interactions,
                                                                         cluster_interaction_column_name)

        result = pd.concat([result, cluster_interaction_result], axis=1)
    result['receptor_entry_name'] = enabled_interactions['entry_name_receptors']
    result['ligand_entry_name'] = enabled_interactions['entry_name_ligands']
    result['ligand_iuphar'] = enabled_interactions['ligand_ligands']
    result['ligand_secreted'] = enabled_interactions['secretion_ligands']
    result['source'] = enabled_interactions['source']
    result['interaction_ratio'] = result[cluster_interactions_columns_names].apply(
        lambda row: sum(row.astype('bool')) / len(cluster_interactions_columns_names), axis=1)
    return result


def _add_is_receptor_property(cluster_counts):
    def is_receptor(multidata):
        if multidata['receptor'] and multidata['transmembrane']:
            return True

        return False

    cluster_counts['is_receptor'] = cluster_counts.apply(is_receptor, axis=1)

    return cluster_counts


def _add_is_ligand_property(cluster_counts):
    def is_ligand(multidata):

        if multidata['secretion'] and not multidata['other']:
            return True

        if multidata['transmembrane'] and not multidata['secretion'] and multidata['extracellular'] and \
                not multidata['cytoplasm'] and not multidata['other'] and not multidata['transporter']:
            return True

        return False

    cluster_counts['is_ligand'] = cluster_counts.apply(is_ligand, axis=1)

    return cluster_counts


def _cellphone_genes(cluster_counts):
    '''
    Merges cluster genes with CellPhoneDB values
    :type cluster_counts: pd.DataFrame()
    :rtype: pd.DataFrame()
    '''
    gene_protein_query = db.session.query(Gene.ensembl, Gene.gene_name, Protein.entry_name, Multidata.id,
                                          Multidata.receptor, Multidata.other,
                                          Multidata.transmembrane, Multidata.transporter, Multidata.cytoplasm,
                                          Multidata.secretion, Multidata.name, Multidata.extracellular,
                                          Multidata.ligand).join(Protein).join(Multidata)
    gene_protein_df = pd.read_sql(gene_protein_query.statement, db.engine)

    gene_protein_df.rename(columns={'id': 'multidata_id'}, inplace=True)

    multidata_counts = pd.merge(cluster_counts, gene_protein_df, left_index=True, right_on='ensembl')

    return multidata_counts


def _get_complex_involved(multidata_counts, clusters_names):
    '''
    Gets complexes involved in counts
    :type multidata_counts: pd.DataFrame()
    :rtype: pd.DataFrame
    '''

    complex_composition_query = db.session.query(ComplexComposition.protein_multidata_id,
                                                 ComplexComposition.complex_multidata_id,
                                                 ComplexComposition.total_protein)
    complex_composition_df = pd.read_sql(complex_composition_query.statement, db.engine)

    complex_counts_composition = pd.merge(complex_composition_df, multidata_counts, left_on='protein_multidata_id',
                                          right_on='multidata_id')

    def all_protein_involved(complex):
        number_proteins_in_counts = len(
            complex_counts_composition[
                complex_counts_composition['complex_multidata_id'] == complex['complex_multidata_id']])

        if number_proteins_in_counts < complex['total_protein']:
            return False

        return True

    complex_counts_composition = complex_counts_composition[
        complex_counts_composition.apply(all_protein_involved, axis=1)]

    complex_multidata_query = db.session.query(Multidata.id, Multidata.receptor, Multidata.other,
                                               Multidata.transmembrane, Multidata.transporter, Multidata.cytoplasm,
                                               Multidata.secretion, Multidata.name, Multidata.extracellular,
                                               Multidata.ligand).join(Complex)
    complex_multidata_df = pd.read_sql(complex_multidata_query.statement, db.engine)
    complex_multidata_df.rename(columns={'id': 'multidata_id'}, inplace=True)

    complex_counts_composition = pd.merge(complex_counts_composition, complex_multidata_df,
                                          left_on='complex_multidata_id',
                                          right_on='multidata_id',
                                          suffixes=['_protein', ''])

    def set_complex_cluster_counts(row):
        scores_complex = complex_counts_composition[
            row['complex_multidata_id'] == complex_counts_composition['complex_multidata_id']]

        for cluster_name in clusters_names:
            row[cluster_name] = scores_complex[cluster_name].min()
        return row

    complex_counts = complex_counts_composition.drop_duplicates(['complex_multidata_id'])

    complex_counts = complex_counts.apply(set_complex_cluster_counts, axis=1)

    complex_counts = complex_counts[list(clusters_names) + ['multidata_id', 'receptor', 'other', 'transmembrane',
                                                            'transporter', 'cytoplasm', 'secretion', 'name']]

    complex_counts['is_complex'] = True

    return complex_counts


def _apply_threshold(cluster_counts, cluster_names, threshold):
    '''
    Sets to 0 minor value colunts than threshold
    :type cluster_counts: pd.DataFrame()
    :type cluster_names: list
    :type threshold: float
    :rtype: pd.DataFrame()
    '''
    cluster_counts_filtered = cluster_counts.copy()

    for cluster_name in cluster_names:
        cluster_counts_filtered.loc[
            cluster_counts_filtered[cluster_name] < float(threshold), [cluster_name]] = 0.0

    return cluster_counts_filtered


def _get_all_cluster_interactions(cluster_names):
    return list(itertools.product(cluster_names, repeat=2))


def _check_receptor_ligand_interactions(cluster_interaction, enabled_interactions, clusters_interaction_name):
    receptor_cluster_name = cluster_interaction[0]
    ligand_cluster_name = cluster_interaction[1]

    def get_relation_score(row):
        count_receptor = row['%s_receptors' % receptor_cluster_name]
        count_ligand = row['%s_ligands' % ligand_cluster_name]

        row[clusters_interaction_name] = min(count_receptor, count_ligand)
        return row

    result = enabled_interactions.apply(get_relation_score, axis=1)

    result.to_csv('out/TEST_cluster_interactions.csv', index=False)

    return result[[clusters_interaction_name]]


def _get_enabled_interactions(cluster_counts, interactions, min_score_2):
    multidata_receptors = cluster_counts[cluster_counts['is_receptor']]
    multidata_ligands = cluster_counts[cluster_counts['is_ligand']]

    receptor_interactions = pd.merge(multidata_receptors, interactions, left_on='multidata_id',
                                     right_on='multidata_1_id')
    enabled_interactions = pd.merge(multidata_ligands, receptor_interactions, left_on='multidata_id',
                                    right_on='multidata_2_id', suffixes=['_ligands', '_receptors'])

    receptor_interactions_inverted = pd.merge(multidata_receptors, interactions, left_on='multidata_id',
                                              right_on='multidata_2_id')
    enabled_interactions_inverted = pd.merge(multidata_ligands, receptor_interactions_inverted, left_on='multidata_id',
                                             right_on='multidata_1_id', suffixes=['_ligands', '_receptors'])

    enabled_interactions = enabled_interactions.append(enabled_interactions_inverted)

    enabled_interactions = enabled_interactions[enabled_interactions['score_2'] > min_score_2]
    return enabled_interactions


def _get_interactions():
    interactions_query = db.session.query(Interaction)
    interactions_df = pd.read_sql(interactions_query.statement, db.engine)

    multidata_query = db.session.query(Multidata.id)
    multidata_df = pd.read_sql(multidata_query.statement, db.engine)

    interactions_df.rename(columns={'id': 'interaction_id'}, index=str, inplace=True)
    interactions_df = pd.merge(interactions_df, multidata_df, left_on=['multidata_1_id'], right_on=['id'])
    interactions_df = pd.merge(interactions_df, multidata_df, left_on=['multidata_2_id'], right_on=['id'],
                               suffixes=['_1', '_2'])

    interactions_df.drop(['id_1', 'id_2'], axis=1, inplace=True)

    return interactions_df